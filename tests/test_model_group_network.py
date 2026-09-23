"""Peer control-channel tests. Loopback TLS is not multi-node GPU acceptance."""
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import socket
import ssl
import struct
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import pytest

from pantheon.models import group_network as network


def document():
    return dict(protocol=1, owner='f_' + 'a' * 16, group_id='test-model-group',
                model_sha256='b' * 64, launch_sha256='c' * 64, members=[
        dict(rank=0, node_id='n_first', generation=1, address='fd12::1', port=18400),
        dict(rank=1, node_id='n_second', generation=2, address='fd12::2', port=18400)])


def test_topology_is_canonical_immutable_and_changes_with_each_binding():
    value = document()
    original = network.PeerTopology(value)
    reordered = {**value, 'members': list(reversed(value['members']))}
    assert network.PeerTopology(reordered).fingerprint == original.fingerprint
    value['members'][0]['generation'] = 99
    original.member(0)['generation'] = 100
    original.document()['members'].clear()
    assert original.member(0)['generation'] == 1
    for key, changed in [('owner', 'f_' + 'd' * 16), ('group_id', 'new-group'),
                         ('model_sha256', 'd' * 64), ('launch_sha256', 'e' * 64)]:
        variant = document()
        variant[key] = changed
        assert network.PeerTopology(variant).fingerprint != original.fingerprint
    for key, changed in [('rank', 1), ('node_id', 'other'), ('generation', 3),
                         ('address', 'fd12::3'), ('port', 18401)]:
        variant = document()
        variant['members'][0][key] = changed
        if key == 'rank':
            variant['members'][1]['rank'] = 0
        assert network.PeerTopology(variant).fingerprint != original.fingerprint


@pytest.mark.parametrize('address', ['0.0.0.0', '::', '127.0.0.1', '::1', '8.8.8.8',
    '169.254.169.254', 'fe80::1', 'fd12::1%eth0', '::ffff:10.0.0.1', '192.0.2.1',
    '100.64.0.1', 'some-host', 'https://10.0.0.1', '224.0.0.1'])
def test_special_public_dns_and_scoped_addresses_are_rejected(address):
    value = document()
    value['members'][0]['address'] = address
    with pytest.raises(ValueError):
        network.PeerTopology(value)


@pytest.mark.parametrize('field,value', [('rank', True), ('rank', -1), ('generation', True),
    ('generation', 0), ('port', True), ('port', 0), ('port', 65536), ('node_id', '')])
def test_invalid_rank_identity_rejected(field, value):
    row = document()
    row['members'][0][field] = value
    with pytest.raises(ValueError):
        network.PeerTopology(row)


def test_duplicate_missing_or_secret_fields_rejected():
    for change in ('rank', 'node_id', 'endpoint', 'extra', 'missing', 'secret', 'protocol'):
        value = document()
        if change in ('rank', 'node_id'):
            value['members'][1][change] = value['members'][0][change]
        elif change == 'endpoint':
            value['members'][1]['address'] = value['members'][0]['address']
        elif change == 'extra':
            value['members'][1]['command'] = 'run-anything'
        elif change == 'missing':
            value['members'].pop()
        elif change == 'secret':
            value['private_key'] = 'do not persist'
        else:
            value['protocol'] = True
        with pytest.raises(ValueError):
            network.PeerTopology(value)


def issue(tmp_path, topology):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Ephemeral test group')])

    def base(name, key):
        return (x509.CertificateBuilder().subject_name(name).issuer_name(issuer)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(minutes=5)))

    ca = (base(issuer, ca_key).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .sign(ca_key, hashes.SHA256()))
    ca_path = tmp_path / 'ca.pem'
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    contexts = []
    for rank in range(len(topology.document()['members'])):
        key = ec.generate_private_key(ec.SECP256R1())
        name = topology.certificate_name(rank)
        cert = (base(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'Fleet rank {rank}')]), key)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
                .add_extension(x509.ExtendedKeyUsage([
                    ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), False)
                .sign(ca_key, hashes.SHA256()))
        cert_path, key_path = tmp_path / f'{rank}.crt', tmp_path / f'{rank}.key'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.touch(mode=0o600)
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        contexts.append(network.tls_contexts(ca_path, cert_path, key_path))
    return contexts


@pytest.fixture
def transport(tmp_path, monkeypatch):
    topology = network.PeerTopology(document())
    contexts = issue(tmp_path, topology)
    original = socket.create_connection
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as pool:
        # Unit tests deliberately map the two declared addresses to loopback.
        # The separate Modal acceptance uses the real private-address listener.
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)

        def connect(address, timeout):
            assert address == ('fd12::2', 18400)
            return original(listener.getsockname(), timeout=timeout)

        monkeypatch.setattr(network.socket, 'create_connection', connect)
        yield topology, contexts, listener, pool


def test_real_tls_authenticates_both_ranks(transport):
    topology, contexts, listener, pool = transport
    future = pool.submit(network.accept_peer, listener, contexts[1][0], topology, 1, timeout=2)
    reply = network.probe_peer(contexts[0][1], topology, 0, 1, timeout=2)
    incoming = future.result(timeout=3)
    assert reply['rank'] == 1 and incoming['rank'] == 0
    assert incoming['tls'] == reply['tls'] == 'TLSv1.3'
    assert incoming['topology'] == reply['topology'] == topology.fingerprint


@pytest.mark.parametrize('change', ['generation', 'model', 'settings', 'owner'])
def test_changed_binding_cannot_reuse_certificates(transport, change):
    topology, contexts, listener, pool = transport
    value = topology.document()
    if change == 'generation':
        value['members'][0]['generation'] += 1
    else:
        field = dict(model='model_sha256', settings='launch_sha256', owner='owner')[change]
        value[field] = 'f_' + 'e' * 16 if change == 'owner' else 'e' * 64
    altered = network.PeerTopology(value)
    future = pool.submit(network.accept_peer, listener, contexts[1][0], altered, 1, timeout=2)
    with pytest.raises(ssl.SSLError):
        network.probe_peer(contexts[0][1], altered, 0, 1, timeout=2)
    with pytest.raises(ssl.SSLError):
        future.result(timeout=3)


def test_same_rank_certificate_cannot_impersonate_other_peer(transport):
    topology, contexts, listener, pool = transport
    future = pool.submit(network.accept_peer, listener, contexts[1][0], topology, 1, timeout=2)
    with pytest.raises((network.PeerMismatch, ssl.SSLError)):
        network.probe_peer(contexts[1][1], topology, 0, 1, timeout=2)
    with pytest.raises(network.PeerMismatch, match='own peer'):
        future.result(timeout=3)


def test_untrusted_ca_rejected(transport, tmp_path):
    topology, contexts, listener, pool = transport
    other = tmp_path / 'other'
    other.mkdir()
    foreign = issue(other, topology)
    foreign[0][1].load_verify_locations(cafile=str(tmp_path / 'ca.pem'))
    future = pool.submit(network.accept_peer, listener, contexts[1][0], topology, 1, timeout=2)
    with pytest.raises(ssl.SSLError):
        network.probe_peer(foreign[0][1], topology, 0, 1, timeout=2)
    with pytest.raises(ssl.SSLError):
        future.result(timeout=3)


def test_server_rejects_forged_sender_and_extra_data(transport):
    topology, contexts, listener, pool = transport
    future = pool.submit(network.accept_peer, listener, contexts[1][0], topology, 1, timeout=2)
    with socket.create_connection(('fd12::2', 18400), timeout=2) as raw:
        with contexts[0][1].wrap_socket(raw, server_hostname=topology.certificate_name(1)) as client:
            value = network._message(topology, 1, 1, 'a' * 64)
            network._send(client, value, time.monotonic() + 2)
            with pytest.raises(network.PeerMismatch):
                network._read(client, time.monotonic() + 2)
    with pytest.raises(network.PeerMismatch, match='identity'):
        future.result(timeout=3)


def test_stale_challenge_response_and_boolean_wire_rank_rejected():
    topology = network.PeerTopology(document())
    expected = network._message(topology, 0, 1, 'a' * 64)
    for value in ({**expected, 'nonce': 'b' * 64}, {**expected, 'protocol': True},
                  {**expected, 'receiver': True}, {**expected, 'extra': 'anything'}):
        with pytest.raises(network.PeerMismatch):
            network._check_message(value, expected)


def test_unverified_and_downgraded_contexts_rejected(transport):
    topology, contexts, listener, _ = transport
    server, client = contexts[0]
    client.check_hostname = False
    with pytest.raises(ValueError, match='verified'):
        network.probe_peer(client, topology, 0, 1)
    client.check_hostname = True
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    with pytest.raises(ValueError, match='TLS 1.3'):
        network.probe_peer(client, topology, 0, 1)
    server.verify_mode = ssl.CERT_OPTIONAL
    with pytest.raises(ValueError, match='TLS 1.3'):
        network.accept_peer(listener, server, topology, 0)


@pytest.mark.parametrize('timeout', [0, -1, True, float('inf'), float('nan'), 61])
def test_invalid_deadlines_rejected_before_connect(transport, timeout):
    topology, contexts, _, _ = transport
    with pytest.raises(ValueError, match='timeout'):
        network.probe_peer(contexts[0][1], topology, 0, 1, timeout=timeout)


def test_fragmented_bounded_frames_and_incomplete_disconnect():
    with ThreadPoolExecutor(max_workers=1) as pool:
        left, right = socket.socketpair()
        with left, right:
            value = {'message': 'fragmented'}
            data = json.dumps(value).encode()
            data = struct.pack('!I', len(data)) + data
            reading = pool.submit(network._read, right, time.monotonic() + 2)
            for byte in data:
                left.sendall(bytes([byte]))
            assert reading.result(timeout=3) == value
            left.sendall(struct.pack('!I', 20) + b'{')
            left.shutdown(socket.SHUT_WR)
            with pytest.raises(network.PeerMismatch, match='incomplete'):
                network._read(right, time.monotonic() + 2)


@pytest.mark.parametrize('size', [0, 4097, 2**32 - 1])
def test_oversized_frames_rejected_before_body(size):
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack('!I', size))
        with pytest.raises(network.PeerMismatch, match='size'):
            network._read(right, time.monotonic() + 1)


def test_stalled_peer_obeys_total_deadline():
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack('!I', 20) + b'{')
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            network._read(right, start + .05)
        assert time.monotonic() - start < 1


def test_listener_binds_exact_private_ip_and_closes(monkeypatch):
    calls = []

    class Listener:
        def __init__(self, *args): calls.append(args)
        def __enter__(self): return self
        def __exit__(self, *args): calls.append('closed')
        def bind(self, address): calls.append(address)
        def listen(self, count): calls.append(count)

    monkeypatch.setattr(network.socket, 'socket', Listener)
    with network.listen(network.PeerTopology(document()), 0):
        pass
    assert calls == [(socket.AF_INET6, socket.SOCK_STREAM), ('fd12::1', 18400), 1, 'closed']
