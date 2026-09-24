"""Pinned Linux SGLang group supervisor entrypoint and authenticated readiness.

Internal artifact entrypoint: group creation, network admission and leader
inference publication remain the controller's responsibility. Rank zero uses
the normal model connector transport after explicit owner activation; workers
expose only /ready. SGLang binds a separate loopback-only port.
"""
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
from urllib.request import Request, urlopen

from group_mesh import PeerMesh
from group_model import check_record, check_files
from group_supervisor import credentials, LinuxEngine, Supervisor
import sglang_group
import sglang_runtime


def json_file(path, limit):
    with Path(path).open('rb') as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Pinned group configuration exceeds its bound')
    return json.loads(content)


def local_interface(member):
    """Verify binding, not network isolation. No public-interface fallback."""
    interface, address = member['interface'], ipaddress.ip_address(member['address'])
    if interface not in {name for _, name in socket.if_nameindex()}:
        raise ValueError('Original private network interface is missing')
    if address.version == 6:
        for row in Path('/proc/net/if_inet6').read_text().splitlines():
            fields = row.split()
            if fields[-1] == interface and int(fields[0], 16) == int(address):
                return
    else:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            result = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack('256s', interface.encode()))
            if socket.inet_ntoa(result[20:24]) == str(address):
                return
    raise ValueError('Original private address is no longer assigned to this interface')


def assigned_capacities(member):
    totals = []
    for device in member['resources']['devices']:
        result = subprocess.run(['nvidia-smi', '-i', device['id'], '--query-gpu=uuid,memory.total',
            '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10)
        rows = result.stdout.strip().splitlines()
        values = [v.strip() for v in rows[0].split(',')] if len(rows) == 1 else []
        if len(values) != 2 or values[0] != device['id']:
            raise ValueError('Original reserved GPU identity changed')
        totals.append(int(values[1]) << 20)
    return totals


def layout(plan, environment):
    """Container ranks see fixed mounts; process ranks use node paths/ports."""
    if 'network_mode' not in plan:
        return None, Path('/fleet/weights'), Path('/fleet/state'), 30000
    engine = int(environment['PANTHEON_PORT_ENGINE'])
    weights = Path(environment['PANTHEON_APP_CACHE']) / 'snapshots' / plan['model_sha256']
    home = Path(environment['HOME'])
    if not 1024 <= engine <= 65535 or not weights.is_absolute() or not home.is_absolute():
        raise ValueError('Missing Fleet process layout for this rank')
    return dict(weights=str(weights), port=engine, home=str(home)), weights, home, engine


def platform_address(member, environment):
    """The provider address must still be the one frozen into the plan."""
    address = environment.get('PANTHEON_GROUP_PLATFORM_ADDRESS', '')
    try:
        same = ipaddress.ip_address(address) == ipaddress.ip_address(member['address'])
    except ValueError:
        same = False
    if not same or environment.get('PANTHEON_GROUP_PLATFORM_INTERFACE') != member['interface']:
        raise ValueError('This node no longer has the planned provider address; recreate the group')


def build(plan, record, environment, local=None, engine_port=30000):
    # Validate the complete plan before inspecting rank indexes, interface names
    # or GPU UUIDs. The peer bundle is tied to the canonical compiled topology.
    members, _, _, topology = sglang_group._validate(plan, record)
    raw = json_file(Path(environment['PANTHEON_GROUP_CREDENTIALS']) / 'group-peer.json', 16384)
    rank = raw.get('rank') if isinstance(raw, dict) else None
    topology.member(rank)
    member = members[rank]
    topology, checked_rank, server, client = credentials(environment['PANTHEON_GROUP_CREDENTIALS'], environment, topology.document())
    if checked_rank != rank:
        raise ValueError('Original rank changed while loading credentials')
    if local is not None:
        platform_address(member, environment)
    local_interface(member)
    launch = sglang_group.rank_launch(plan, record, rank, assigned_capacities(member), local)
    child_env = sglang_group.environment(environment, launch)
    # The model engine itself has no reason to read peer keys or service tokens.
    child_env = {key: value for key, value in child_env.items()
                 if not key.startswith('PANTHEON_GROUP_') and key != 'PANTHEON_MODEL_CREDENTIALS'}
    mesh = PeerMesh(topology, rank, server, client, startup_timeout=120, peer_timeout=10, interval=1)
    engine = LinuxEngine(launch['argv'], child_env)
    run = Supervisor(mesh, engine, lambda: probe_engine(engine, record['sha256'], rank, engine_port))
    return run, topology, rank


def probe_engine(engine, model_sha, rank, port=30000):
    original = engine.listener_identity(port)
    if original is None:
        raise ValueError('Original engine has no owned loopback listener')
    sglang_group.ready_rank(port, model_sha, rank)
    if engine.listener_identity(port) != original:
        raise ValueError('Original engine listener changed during readiness probe')


class StatusServer(HTTPServer):
    allow_reuse_address = False

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(1)
        return connection, address


def serve_status(run, port, token, identity):
    if not isinstance(token, str) or not 16 <= len(token) <= 512:
        raise ValueError('Missing generation-bound Fleet status credential')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            if not hmac.compare_digest(self.headers.get('X-Pantheon-App-Token', '').encode(), token.encode()):
                status, value = 401, {'error': 'Unauthorized'}
            elif self.path != '/ready':
                status, value = 404, {'error': 'Unknown status route'}
            else:
                ready = run.ready()
                status, value = (200 if ready else 503), dict(protocol=1, ready=ready, **identity)
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
    server = StatusServer(('127.0.0.1', port), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
    thread.start()
    return server, thread


def main():
    environment = dict(os.environ)
    port = int(environment['PANTHEON_PORT_HTTP'])
    token = environment['PANTHEON_APP_RPC_TOKEN']
    instance = environment['PANTHEON_INSTANCE_ID']
    generation = environment['PANTHEON_INSTANCE_GENERATION']
    if (not 1024 <= port <= 65535 or port == 30000
            or re.fullmatch('[a-f0-9]{32}', instance) is None
            or not generation.isdecimal() or not 1 <= int(generation) < 2**63):
        raise ValueError('Missing original Fleet launch identity/port')
    identity = dict(instance_id=instance, generation=int(generation))
    if sys.argv[1:] == ['ready']:
        request = Request(f'http://127.0.0.1:{port}/ready', headers={'X-Pantheon-App-Token': token})
        with urlopen(request, timeout=2) as response:
            data = response.read(4097)
            if len(data) > 4096 or json.loads(data) != dict(protocol=1, ready=True, **identity):
                raise ValueError('Original model cohort is not ready')
        print('{"status":"succeeded"}')
        return
    if sys.argv[1:] != ['start'] or sys.platform != 'linux' or version('sglang') != sglang_runtime.VERSION:
        raise ValueError('Requires the pinned Linux SGLang group recipe')
    plan = json_file(Path(__file__).with_name('group-plan.json'), 65536)
    local, weights, state, engine_port = layout(plan, environment)
    record = json_file(weights / 'snapshot.json', 2 << 20)
    check_record(record, json_file(Path(__file__).with_name('group-model.json'), 2 << 20))
    check_files(str(weights), record)
    control = {m['control_port'] for m in plan['members']}
    if len({port, engine_port, plan['rendezvous_port']}) != 3 or control & {port, engine_port, plan['rendezvous_port']}:
        raise ValueError('Readiness and private collective/control ports must be distinct')
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: cancelled.set())
    run, _, rank = build(plan, record, environment, local, engine_port)
    if rank == 0:
        from group_connector import GroupConnector, serve
        connector = GroupConnector(str(state / 'group-connector'), run, plan, record, identity, token)
        server, thread = serve(connector, port)
    else:
        server, thread = serve_status(run, port, token, identity)
    try:
        run.run(cancelled)
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
        if thread.is_alive():
            raise RuntimeError('Group status listener did not stop')


if __name__ == '__main__':
    main()
