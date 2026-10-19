"""Private, mutually authenticated peer preflight for an explicit model group.

This is a control-channel primitive, not an NCCL tunnel, startup barrier or
inference-readiness signal. Callers own listener lifetime and certificate delivery.
The roster must come from authenticated group intent, never a peer announcement.
Only stdlib is required on nodes; no certificate authority is installed globally.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import ipaddress
import json
import re
import secrets
import socket
import ssl
import struct
import time


class PeerMismatch(ValueError):
    """The peer is not a member of this exact group launch."""


def _matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


class PeerTopology:
    """Immutable canonical roster, including the hash of shared model settings.

    `launch_sha256` must cover the pinned engine, model format/quantization,
    global TP, context/parallel limits, resource assignments and rendezvous plan.
    This class binds that hash; it does not invent or validate an engine plan.
    Member generation is the exact started generation, not a stopped predecessor.
    """
    def __init__(self, document):
        keys = {'protocol', 'owner', 'group_id', 'model_sha256', 'launch_sha256', 'members'}
        if not isinstance(document, dict) or set(document) != keys:
            raise ValueError('Declare the exact owner, group, model, launch and peer roster')
        if (type(document['protocol']) is not int or document['protocol'] != 1
                or not _matches(document['owner'], r'f_[a-f0-9]{16}')
                or not _matches(document['group_id'], r'[a-z0-9][a-z0-9_-]{0,63}')
                or any(not _matches(document[key], r'[a-f0-9]{64}')
                       for key in ('model_sha256', 'launch_sha256'))):
            raise ValueError('Invalid group identity')
        members = document['members']
        if not isinstance(members, list) or not 2 <= len(members) <= 16:
            raise ValueError('Declare 2..16 explicit peers')
        normalized, nodes, endpoints, ranks = [], set(), set(), set()
        networks = tuple(ipaddress.ip_network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))
        for member in members:
            if not isinstance(member, dict) or set(member) != {
                    'rank', 'node_id', 'generation', 'address', 'port'}:
                raise ValueError('Pin each rank, node, generation and private control endpoint')
            rank, node, generation, address, port = (member[key] for key in
                ('rank', 'node_id', 'generation', 'address', 'port'))
            if (type(rank) is not int or not 0 <= rank < len(members) or rank in ranks
                    or not _matches(node, r'[A-Za-z0-9_-]{1,100}') or node in nodes
                    or type(generation) is not int or not 1 <= generation < 2**63
                    or type(port) is not int or not 1024 <= port <= 65535
                    or not isinstance(address, str) or '%' in address):
                raise ValueError('Invalid or duplicate peer identity')
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                raise ValueError('Peer address must be a private IP literal') from None
            # is_private also includes special-purpose/loopback ranges. Permit
            # only RFC1918 and ULA, never metadata, mapped IPv4, DNS or wildcards.
            if not any(ip.version == net.version and ip in net for net in networks):
                raise ValueError('Peer address must use RFC1918 or ULA private networking')
            endpoint = (str(ip), port)
            if endpoint in endpoints:
                raise ValueError('Peer endpoints must be distinct')
            normalized.append({**member, 'address': str(ip)})
            nodes.add(node)
            endpoints.add(endpoint)
            ranks.add(rank)
        self._document = {**deepcopy(document), 'members': sorted(normalized, key=lambda m: m['rank'])}
        encoded = json.dumps(self._document, sort_keys=True, separators=(',', ':')).encode()
        self._fingerprint = hashlib.sha256(encoded).hexdigest()

    @property
    def fingerprint(self):
        return self._fingerprint

    def document(self):
        return deepcopy(self._document)

    def member(self, rank):
        if type(rank) is not int or not 0 <= rank < len(self._document['members']):
            raise ValueError('Unknown peer rank')
        return deepcopy(self._document['members'][rank])

    def certificate_name(self, rank):
        self.member(rank)
        digest = self.fingerprint
        return f'r{rank}.{digest[:32]}.{digest[32:]}.fleet-model.invalid'

    def certificate_rank(self, certificate):
        # Require one exact DNS SAN, on both sides; no CN fallback or wildcard.
        names = certificate.get('subjectAltName', ())
        for member in self._document['members']:
            if tuple(names) == (('DNS', self.certificate_name(member['rank'])),):
                return member['rank']
        raise PeerMismatch('Peer certificate does not match this exact group topology')


def tls_contexts(ca_file, certificate_file, key_file):
    """Load node-local, group-scoped material. Returned contexts require mTLS."""
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for context in (server, client):
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cafile=str(ca_file))
        context.load_cert_chain(str(certificate_file), str(key_file))
        context.hostname_checks_common_name = False
    client.check_hostname = True
    return server, client


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Peer preflight deadline exceeded')
    return remaining


def _check_context(context, timeout, *, server):
    protocol = ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT
    if (type(timeout) not in (int, float) or not 0 < timeout <= 60
            or context.protocol != protocol or context.verify_mode != ssl.CERT_REQUIRED
            or context.minimum_version < ssl.TLSVersion.TLSv1_3
            or (not server and not context.check_hostname)):
        raise ValueError('Bounded timeout and verified, mutually authenticated TLS 1.3 are required')


def _read_exact(connection, count, deadline):
    output = bytearray()
    while len(output) < count:
        connection.settimeout(_remaining(deadline))
        part = connection.recv(count - len(output))
        if not part:
            raise PeerMismatch('Peer closed an incomplete preflight frame')
        output.extend(part)
    return bytes(output)


def _read(connection, deadline):
    size = struct.unpack('!I', _read_exact(connection, 4, deadline))[0]
    if not 0 < size <= 4096:
        raise PeerMismatch('Invalid preflight frame size')
    data = _read_exact(connection, size, deadline)
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, ValueError):
        raise PeerMismatch('Invalid preflight JSON') from None
    if not isinstance(value, dict):
        raise PeerMismatch('Invalid preflight message')
    return value


def _send(connection, value, deadline):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    if len(data) > 4096:
        raise ValueError('Preflight message exceeds its bound')
    connection.settimeout(_remaining(deadline))
    connection.sendall(struct.pack('!I', len(data)) + data)


def _message(topology, sender, receiver, nonce):
    return dict(protocol=1, topology=topology.fingerprint, sender=sender, receiver=receiver, nonce=nonce)


def _check_message(value, expected):
    # Python considers True == 1; wire ranks and protocol must remain integers.
    if value != expected or any(type(value.get(key)) is not int for key in ('protocol', 'sender', 'receiver')):
        raise PeerMismatch('Peer preflight identity or challenge changed')


@contextmanager
def listen(topology, rank):
    """Bind only the pinned private address. Caller must bound the accept loop."""
    member = topology.member(rank)
    family = socket.AF_INET6 if ':' in member['address'] else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((member['address'], member['port']))
        listener.listen(len(topology.document()['members']) - 1)
        yield listener


def accept_peer(listener, context, topology, rank, *, timeout=5):
    """Authenticate one incoming peer; no lifecycle action or hidden retry."""
    topology.member(rank)
    _check_context(context, timeout, server=True)
    deadline = time.monotonic() + timeout
    listener.settimeout(_remaining(deadline))
    raw, _ = listener.accept()
    with raw:
        raw.settimeout(_remaining(deadline))
        with context.wrap_socket(raw, server_side=True) as connection:
            peer = topology.certificate_rank(connection.getpeercert())
            if peer == rank:
                raise PeerMismatch('A rank cannot satisfy its own peer check')
            value = _read(connection, deadline)
            nonce = value.get('nonce')
            if not _matches(nonce, r'[a-f0-9]{64}'):
                raise PeerMismatch('Invalid preflight challenge')
            _check_message(value, _message(topology, peer, rank, nonce))
            _send(connection, _message(topology, rank, peer, nonce), deadline)
            return dict(rank=peer, topology=topology.fingerprint, tls=connection.version())


def probe_peer(context, topology, rank, peer, *, timeout=5):
    """Authenticate an exact outgoing peer with a fresh, bounded challenge."""
    topology.member(rank)
    member = topology.member(peer)
    _check_context(context, timeout, server=False)
    if rank == peer:
        raise ValueError('Distinct peer ranks are required')
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    with socket.create_connection((member['address'], member['port']), timeout=_remaining(deadline)) as raw:
        raw.settimeout(_remaining(deadline))
        with context.wrap_socket(raw, server_hostname=topology.certificate_name(peer)) as connection:
            if topology.certificate_rank(connection.getpeercert()) != peer:
                raise PeerMismatch('Unexpected peer certificate')
            nonce = secrets.token_hex(32)
            _send(connection, _message(topology, rank, peer, nonce), deadline)
            _check_message(_read(connection, deadline), _message(topology, peer, rank, nonce))
            return dict(rank=peer, topology=topology.fingerprint, tls=connection.version(),
                        roundtrip_ms=round((time.monotonic() - started) * 1000, 3))


def private_endpoint(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Declare a canonical private UDP endpoint')
    match = re.fullmatch(r'\[([^\]]+)\]:([0-9]+)|([^:]+):([0-9]+)', value)
    if not match:
        raise ValueError('Declare a canonical private UDP endpoint')
    host, port = (match[1], match[2]) if match[1] else (match[3], match[4])
    if '%' in host:
        raise ValueError('Scoped UDP endpoints are unsupported')
    ip = ipaddress.ip_address(host)
    networks = map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))
    canonical = f'[{ip}]:{int(port)}' if ip.version == 6 else f'{ip}:{int(port)}'
    if (canonical != value or not 1024 <= int(port) <= 65535
            or not any(ip.version == n.version and ip in n for n in networks)):
        raise ValueError('Use canonical RFC1918 or ULA UDP endpoints with unprivileged ports')
    return value


def addresses(values, count):
    if not isinstance(values, list) or len(values) != count:
        raise ValueError('Pin one host UDP endpoint per rank')
    for value in values:
        private_endpoint(value)
    if len(set(values)) != count:
        raise ValueError('Each rank requires a distinct host UDP endpoint')
    return values
