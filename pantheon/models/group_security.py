"""Public trust binding and enrollment barrier for the group coordinator.

Private keys never transit the Agent or journal. This consumes already pinned
packages and CA; creation intent, package building and engine mounts are separate.
"""
import hashlib
import re

from .group_network import PeerTopology


def validate_security(row):
    security = row.get('peer_security')
    if security is None:
        return None
    if not isinstance(security, dict) or set(security) != {'topology', 'ca_sha256', 'ready', 'closed'}:
        raise ValueError('Declare only public pinned group trust and barrier state')
    peers = PeerTopology(security['topology'])
    doc = peers.document()
    if (doc != security['topology'] or doc['owner'] != row['owner'] or doc['group_id'] != row['group_id']
            or not isinstance(security['ca_sha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', security['ca_sha256'])
            or type(security['ready']) is not bool or type(security['closed']) is not bool):
        raise ValueError('Use the exact canonical topology and pinned CA')
    members = row['members']
    targets = {m['target']['node_id']: m['target'] for m in members}
    if len(targets) != len(members) or set(targets) != {m['node_id'] for m in doc['members']}:
        raise ValueError('Credential roster must match every lifecycle target')
    for peer in doc['members']:
        if peer['generation'] != targets[peer['node_id']]['generation'] + 2:
            raise ValueError('Credential roster must pin each exact started generation')
    if row['phase'] in {'committing', 'ready'} and (not security['ready'] or security['closed']):
        raise ValueError('Install all peer certificates before committing starts')
    if security['closed'] and row['phase'] not in {'aborting', 'stopped'}:
        raise ValueError('Closed authority requires terminal group intent')
    if row['phase'] == 'stopped' and not security['closed']:
        raise ValueError('Confirm the issuance fence before declaring the group stopped')
    return peers


def validate_security_transition(old, new):
    validate_security(new)
    a, b = old.get('peer_security'), new.get('peer_security')
    if (a is None) != (b is None):
        raise ValueError('Group trust cannot be added or removed from an existing intent')
    if a is None:
        return
    if (any(a[k] != b[k] for k in ('topology', 'ca_sha256'))
            or any(a[k] and not b[k] for k in ('ready', 'closed'))):
        raise ValueError('Group trust and acknowledged barriers are immutable')
    if not a['ready'] and b['ready']:
        if new['phase'] not in {'preparing', 'committing'} or not all(
                (m.get('observation') or {}).get('state') == 'prepared' for m in new['members']):
            raise ValueError('Certificate barrier requires every original preparation')


def enrollment_claim(row, peers, member, enrollment):
    target = member['target']
    peer = next(p for p in peers.document()['members'] if p['node_id'] == target['node_id'])
    identity = hashlib.sha256('\0'.join((row['owner'], target['node_id'],
        target['digest'], target['scope'])).encode()).hexdigest()[:32]
    expected = dict(protocol=1, instance_id=identity, revision=target['digest'],
                    generation=peer['generation'], topology_sha256=peers.fingerprint,
                    dns_name=peers.certificate_name(peer['rank']))
    if (not isinstance(enrollment, dict)
            or any(type(enrollment.get(k)) is not type(v) or enrollment[k] != v for k, v in expected.items())
            or type(enrollment.get('certificate_installed')) is not bool
            or not isinstance(enrollment.get('csr_pem'), str)
            or not 0 < len(enrollment['csr_pem']) <= 16384):
        raise ValueError('Enrollment differs from the original prepared group member')
    return dict(rank=peer['rank'], node_id=target['node_id'], instance_id=identity,
                revision=target['digest'], scope=target['scope'], generation=peer['generation'],
                preparation_id=member['prepare']['request']['operation_id'], csr_pem=enrollment['csr_pem'])


async def install_peer(lifecycle, row, peers, member):
    """Called only after inspect_member confirms the exact prepared snapshot.

    These operations are idempotent on their original bindings, unlike engine
    starts. A lost response may retry the same CSR, certificate and installation.
    """
    target = member['target']
    identity = hashlib.sha256('\0'.join((row['owner'], target['node_id'],
        target['digest'], target['scope'])).encode()).hexdigest()[:32]
    binding = dict(node_id=target['node_id'], instance_id=identity,
                   revision=target['digest'], generation=target['generation'] + 1)
    enrolled = await lifecycle.group_peer(binding)
    claim = enrollment_claim(row, peers, member, enrolled)
    issued = await lifecycle.group_authority(peers.member(0)['node_id'], 'issue',
        group_id=row['group_id'], topology_sha256=peers.fingerprint, claim=claim)
    if issued.get('ca_sha256') != row['peer_security']['ca_sha256']:
        raise ValueError('Issuer root differs from the pinned group authority')
    installed = await lifecycle.group_peer(binding, certificate=issued['certificate_pem'], authority=issued['ca_pem'])
    if enrollment_claim(row, peers, member, installed) != claim or installed['certificate_installed'] is not True:
        raise ValueError('Node did not confirm installation for the original key')
    return True
