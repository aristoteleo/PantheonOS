"""Opt-in two-container CPU acceptance of the real Fleet peer preflight module.

uv run --with modal --with cryptography python fleet/scripts/verify-group-private-network.py --output /absolute/new/receipt-directory

No GPUs, public tunnels, installed Fleet nodes or user services are used. The
output directory must be new: inspect original handles rather than duplicate a
possibly running attempt. Ephemeral certificate keys stay in the authenticated
Modal control path and node temp files; they are never written to receipts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import uuid

import modal

app = modal.App('fleet-model-peer-preflight-acceptance')
image = modal.Image.debian_slim(python_version='3.12')
if modal.is_local():
    SOURCE = Path(__file__).resolve().parents[2] / 'pantheon/models/group_network.py'
    image = image.add_local_file(SOURCE, '/opt/fleet-peer/group_network.py', copy=True)
else:
    # Modal imports this script in /root before invoking the function. Never
    # resolve the developer checkout or rebuild local file mounts remotely.
    SOURCE = Path('/opt/fleet-peer/group_network.py')
SOURCE_SHA = hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def wait_value(exchange, key, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = exchange.get(key, None)
        if value is not None:
            return value
        time.sleep(.2)
    raise TimeoutError('Original group peer did not reach its next preflight phase')


@app.function(image=image, cpu=.25, memory=256, region='us-east', i6pn=True,
              timeout=180, startup_timeout=120, retries=0, max_containers=2,
              scaledown_window=2, single_use_containers=True)
def peer(rank, exchange):
    sys.path.insert(0, '/opt/fleet-peer')
    import group_network as network

    address = socket.getaddrinfo('i6pn.modal.local', None, socket.AF_INET6)[0][4][0]
    exchange.put(f'address-{rank}', address)
    config = wait_value(exchange, f'config-{rank}')
    topology = network.PeerTopology(config['topology'])
    if topology.member(rank)['address'] != address:
        raise ValueError('Assigned private address does not match this original container')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ('ca', 'cert', 'key'):
            with os.fdopen(os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
                output.write(config[name])
        server, client = network.tls_contexts(root / 'ca', root / 'cert', root / 'key')
        with network.listen(topology, rank) as listener, ThreadPoolExecutor(max_workers=1) as pool:
            receiving = pool.submit(network.accept_peer, listener, server, topology, rank, timeout=30)
            exchange.put(f'listening-{rank}', True)
            wait_value(exchange, f'listening-{1-rank}', seconds=20)
            outgoing = network.probe_peer(client, topology, rank, 1-rank, timeout=10)
            incoming = receiving.result(timeout=10)
        assert incoming['rank'] == outgoing['rank'] == 1-rank
        assert incoming['topology'] == outgoing['topology'] == topology.fingerprint
        assert incoming['tls'] == outgoing['tls'] == 'TLSv1.3'
        return dict(rank=rank, private_address=address, incoming=incoming, outgoing=outgoing,
                    module_sha256=hashlib.sha256(Path(network.__file__).read_bytes()).hexdigest())


def certificates(topology):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Fleet ephemeral acceptance group')])

    def base(name, key):
        return (x509.CertificateBuilder().subject_name(name).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
            .not_valid_after(now+datetime.timedelta(minutes=10)))

    ca = (base(issuer, ca_key).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .sign(ca_key, hashes.SHA256()))
    rows = []
    for rank in range(2):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (base(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'Fleet rank {rank}')]), key)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(topology.certificate_name(rank))]), False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,
                                                     ExtendedKeyUsageOID.CLIENT_AUTH]), False)
                .sign(ca_key, hashes.SHA256()))
        rows.append(dict(topology=topology.document(), ca=ca.public_bytes(serialization.Encoding.PEM).decode(),
            cert=cert.public_bytes(serialization.Encoding.PEM).decode(),
            key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir()  # Exclusive attempt identity; deliberately not exist_ok.
    spec = importlib.util.spec_from_file_location('group_network', SOURCE)
    network = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(network)
    handle = dict(state='starting', calls=[], gpu=False, public_tunnels=0, module_sha256=SOURCE_SHA)

    def save():
        temporary = output / 'handle.tmp'
        temporary.write_text(json.dumps(handle, indent=2) + '\n')
        temporary.replace(output / 'handle.json')

    save()
    calls = []
    try:
        with modal.enable_output(), app.run(), modal.Dict.ephemeral() as exchange:
            handle['app_id'] = app.app_id
            save()
            try:
                for rank in range(2):
                    call = peer.spawn(rank, exchange)
                    calls.append(call)
                    handle['calls'].append(call.object_id)
                    save()
                addresses = [wait_value(exchange, f'address-{rank}', seconds=120) for rank in range(2)]
                assert addresses[0] != addresses[1], 'Two different containers are required'
                # Synthetic identities are explicitly confined to this CPU
                # acceptance; no Fleet node registration or lifecycle claim.
                topology = network.PeerTopology(dict(protocol=1, owner='f_' + uuid.uuid4().hex[:16],
                    group_id='peer-acceptance-' + uuid.uuid4().hex[:12], model_sha256='a'*64,
                    launch_sha256=SOURCE_SHA, members=[dict(rank=rank, node_id=f'acceptance-{rank}',
                        generation=1, address=address, port=18407) for rank, address in enumerate(addresses)]))
                for rank, config in enumerate(certificates(topology)):
                    exchange.put(f'config-{rank}', config)
                handle['state'] = 'probing'
                handle['topology_sha256'] = topology.fingerprint
                save()
                rows = [call.get(timeout=60) for call in calls]
                assert all(row['module_sha256'] == SOURCE_SHA for row in rows)
                result = dict(passed=True, topology=topology.document(), rows=rows,
                    scope='Two real CPU containers; control-plane mTLS, not NCCL or GPU inference')
                (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
                print(json.dumps(result), flush=True)
            finally:
                # Attempt every cleanup even if one cancellation call fails.
                errors = []
                for call in calls:
                    try:
                        call.cancel(terminate_containers=True)
                    except Exception as error:
                        errors.append(type(error).__name__)
                handle['state'] = 'cleanup-pending' if errors else 'calls-cancelled'
                handle['cleanup_errors'] = errors
                save()
        if not handle['cleanup_errors']:
            handle['state'] = 'app-stopped'
        save()
    except BaseException:
        handle['failed'] = True
        save()
        raise


if __name__ == '__main__':
    main()
