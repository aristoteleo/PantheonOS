"""Public, immutable private-network enrollment before any rank preparation.

Host UDP addresses are explicit underlay endpoints; collective addresses in the
peer topology belong inside the isolated namespace and cannot supply them.
Private WireGuard keys remain in the original node's durable overlay store.
"""
import asyncio
import base64

from .group_network import private_endpoint, addresses


def endpoint(value):
    if (not isinstance(value, dict) or set(value) != {'rank', 'address', 'public_key'}
            or type(value['rank']) is not int or not 0 <= value['rank'] < 16):
        raise ValueError('Declare only rank, private UDP address and public key')
    private_endpoint(value['address'])
    key = value['public_key']
    if not isinstance(key, str) or len(key) != 44:
        raise ValueError('Declare a canonical public X25519 key')
    try:
        raw = base64.b64decode(key, validate=True)
    except ValueError:
        raise ValueError('Declare a canonical public X25519 key') from None
    if len(raw) != 32 or not any(raw) or base64.b64encode(raw).decode() != key:
        raise ValueError('Declare a canonical public X25519 key')
    # The node additionally performs X25519 low-order-key validation before pin.
    return value


PLATFORM_PRIVATE = 'platform-private'


def platform_addresses(values, count):
    """Provider-assigned rank addresses (e.g. Modal i6pn): distinct IPv6 ULAs."""
    import ipaddress
    if not isinstance(values, list) or len(values) != count or len(set(values)) != count:
        raise ValueError('Declare one distinct provider address per rank')
    for value in values:
        if not isinstance(value, str) or ipaddress.ip_address(value) not in ipaddress.ip_network('fc00::/7'):
            raise ValueError('Provider-private ranks must use IPv6 ULA addresses')


def validate_network(network, count):
    if isinstance(network, dict) and network.get('mode') == PLATFORM_PRIVATE:
        # The provider already isolates this network; Fleet pins no keys.
        if (set(network) != {'mode', 'addresses', 'endpoints', 'ready', 'closed'} or network['endpoints'] != []
                or type(network['ready']) is not bool or type(network['closed']) is not bool):
            raise ValueError('Declare exact provider network intent and barriers')
        platform_addresses(network['addresses'], count)
        return
    if (not isinstance(network, dict) or set(network) != {'addresses', 'endpoints', 'ready', 'closed'}
            or type(network['ready']) is not bool or type(network['closed']) is not bool):
        raise ValueError('Declare exact public network intent and barriers')
    addresses(network['addresses'], count)
    roster = network['endpoints']
    if not isinstance(roster, list) or len(roster) not in {0, count}:
        raise ValueError('Persist only the complete public endpoint roster')
    for rank, value in enumerate(roster):
        endpoint(value)
        if value['rank'] != rank or value['address'] != network['addresses'][rank]:
            raise ValueError('Public endpoint roster changed the pinned rank or host address')
    if len({e['public_key'] for e in roster}) != len(roster) or network['ready'] and not roster:
        raise ValueError('Network readiness requires every distinct original public key')


def validate_transition(old, new, row):
    if (old is None) != (new is None):
        raise ValueError('Network intent cannot be added or removed')
    if old is None:
        return
    if (old.get('mode') != new.get('mode') or old['addresses'] != new['addresses']
            or old['endpoints'] and old['endpoints'] != new['endpoints']
            or any(old[k] and not new[k] for k in ('ready', 'closed'))):
        raise ValueError('Original network addresses, keys and acknowledgements are immutable')
    if not old['ready'] and any(m['prepare']['sent'] for m in row['members']):
        raise ValueError('Persist network readiness before claiming rank preparation')
    if old['endpoints'] != new['endpoints'] and row['phase'] != 'preparing':
        raise ValueError('Cancelled network enrollment cannot resume')
    platform = new.get('mode') == PLATFORM_PRIVATE  # provider network: no roster to pin
    if not old['ready'] and new['ready'] and ((not old['endpoints'] and not platform) or row['phase'] != 'preparing'):
        raise ValueError('Persist the complete original roster before pinning it')
    if not old['closed'] and new['closed'] and row['phase'] not in {'aborting', 'stopped'}:
        raise ValueError('Persist terminal intent before closing network enrollment')


def check_reply(reply, node, peers, *, state=None):
    expected = dict(protocol=1, owner=peers.document()['owner'], node_id=node,
        group_id=peers.document()['group_id'], topology_sha256=peers.fingerprint)
    if (not isinstance(reply, dict) or set(reply) - (set(expected) | {'state', 'endpoint', 'endpoints'})
            or any(type(reply.get(k)) is not type(v) or reply[k] != v for k, v in expected.items())
            or reply.get('state') not in {'prepared', 'pinned', 'closed'}
            or state is not None and reply.get('state') != state):
        raise ValueError('Node returned another original overlay identity or state')
    if reply.get('endpoint') is not None:
        local = endpoint(reply['endpoint'])
        if peers.member(local['rank'])['node_id'] != node:
            raise ValueError('Node returned another rank endpoint')
    if reply['state'] != 'closed' and reply.get('endpoint') is None:
        raise ValueError('Live enrollment requires its original public endpoint')
    roster = reply.get('endpoints', [])
    if not isinstance(roster, list):
        raise ValueError('Invalid public endpoint roster')
    if roster:
        if reply['state'] == 'prepared' or reply.get('endpoint') is None:
            raise ValueError('Roster requires a pinned original enrollment')
        if any(not isinstance(e, dict) for e in roster):
            raise ValueError('Invalid public endpoint roster')
        validate_network(dict(addresses=[e.get('address') for e in roster], endpoints=roster,
                              ready=True, closed=False), len(peers.document()['members']))
        if roster[reply['endpoint']['rank']] != reply['endpoint']:
            raise ValueError('Roster replaced the local endpoint')
    if reply['state'] == 'pinned' and not roster:
        raise ValueError('Pinned enrollment requires the complete roster')
    return reply


async def advance_network(lifecycle, row, peers, timeout):
    """At most one enrollment barrier per call; caller must CAS-save the result.

    Never pin keys collected in this same call: they first become durable journal
    intent. Lost responses retry only the original manifest/roster. Node close is
    monotonic, including close-before-prepare races.
    """
    network = row['peer_security']['network']
    if network['closed']:
        return False
    closing = row['phase'] == 'aborting'
    if network.get('mode') == PLATFORM_PRIVATE:
        # No Fleet overlay exists to enroll or close; each rank re-checks its
        # own provider address at start. Barriers keep their journal order.
        if closing:
            network['closed'] = True
            return False
        network['ready'] = True
        return True
    if network['ready'] and not closing:
        return True
    roster = network['endpoints']
    action = 'close' if closing else 'pin' if roster else 'prepare'
    async def one(peer):
        node, rank = peer['node_id'], peer['rank']
        args = dict(topology=peers.document())
        if action == 'prepare':
            args.update(rank=rank, ca_sha256=row['peer_security']['ca_sha256'],
                        address=network['addresses'][rank])
        elif action == 'pin':
            args['endpoints'] = roster
        reply = await asyncio.wait_for(lifecycle.group_overlay(node, action, **args), timeout)
        check_reply(reply, node, peers, state='closed' if closing else 'pinned' if roster else None)
        if not closing:
            if reply['state'] == 'closed' or reply['endpoint']['address'] != network['addresses'][rank]:
                raise ValueError('Original overlay is closed or changed')
            if roster and (reply['endpoints'] != roster or reply['endpoint'] != roster[rank]):
                raise ValueError('Node did not pin the original complete roster')
        return reply
    results = await asyncio.gather(*(one(p) for p in peers.document()['members']), return_exceptions=True)
    if any(isinstance(r, BaseException) for r in results):
        return False
    if closing:
        network['closed'] = True
    elif not roster:
        network['endpoints'] = [r['endpoint'] for r in results]
        validate_network(network, len(results))
    else:
        network['ready'] = True
    return network['ready'] and not network['closed']
