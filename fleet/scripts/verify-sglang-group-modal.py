"""Opt-in, bounded SGLang TP2 across two private Modal L4 containers.

Uses the production supervisor/entrypoint, an immutable image/model, one
GPU per container and no public tunnel. It does not register production Fleet
nodes or claim Docker/Fleet lifecycle or distinct physical-host acceptance.
Run with uv --with modal --with cryptography and a new --output directory.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import signal
import shutil
import ssl
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import uuid

import modal

IMAGE = 'lmsysorg/sglang@sha256:4bf342cb756a7105e6df9ae81abeb62e891ff70d34b83fdd7a891fa46a494eca'
REVISION = '7ae557604adf67be50417f59c2c2f167def9a775'
app = modal.App('fleet-sglang-group-acceptance')
image = modal.Image.from_registry(IMAGE)
if modal.is_local():
    ROOT = Path(__file__).resolve().parents[2]
    image = (image.run_commands(
        f"python3 -c \"from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen2.5-0.5B-Instruct', revision='{REVISION}', local_dir='/opt/model', ignore_patterns=['*.bin', '*.h5', '*.msgpack'])\"")
        .add_local_dir(ROOT / 'apps/model-service', '/opt/connector', copy=True, ignore=['__pycache__'])
        .add_local_file(ROOT / 'pantheon/models/group_network.py', '/opt/connector/group_network.py', copy=True)
        .add_local_file(ROOT / 'pantheon/models/group_mesh.py', '/opt/connector/group_mesh.py', copy=True)
        .add_local_file(ROOT / 'fleet/scripts/prepare-sglang-fixture.py', '/opt/prepare-sglang-fixture.py', copy=True)
        .run_commands('python3 /opt/prepare-sglang-fixture.py'))


def wait_value(exchange, key, seconds, *, abort=True):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if abort and exchange.get('abort', None):
            raise RuntimeError('An original group member failed; do not replace its rank')
        value = exchange.get(key, None)
        if value is not None:
            return value
        time.sleep(.25)
    raise TimeoutError('Original group did not reach ' + key)


def inventory():
    output = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid,memory.total,memory.used',
        '--format=csv,noheader,nounits'], text=True, timeout=10).strip().splitlines()
    if len(output) != 1:
        raise RuntimeError('Expected exactly one isolated L4 GPU')
    device, total, used = [value.strip() for value in output[0].split(',')]
    return dict(id=device, total_bytes=int(total) << 20, used_bytes=int(used) << 20)


def private_interface(address):
    for line in Path('/proc/net/if_inet6').read_text().splitlines():
        fields = line.split()
        if str(ipaddress.IPv6Address(int(fields[0], 16))) == address:
            return fields[-1]
    raise RuntimeError('The private container address has no exact local interface')


def verify_image_snapshot(snapshots, source):
    # Exported image layers may round metadata timestamps. This acceptance
    # verifies all content hashes instead of weakening production's fast cache
    # check or rewriting the original immutable receipt to fit observed files.
    record = snapshots.snapshot('/opt/model-cache', source['sha256'], verify=False)
    directory = Path('/opt/model-cache/snapshots') / source['sha256']
    drift = []
    for item in record['files']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid prepared snapshot path')
        path = directory / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size']:
            raise ValueError('Prepared snapshot file identity changed')
        digest = hashlib.sha256()
        with path.open('rb') as content:
            while chunk := content.read(8 << 20):
                digest.update(chunk)
        if digest.hexdigest() != item['sha256']:
            raise ValueError('Prepared snapshot content hash changed')
        observed = path.stat().st_mtime_ns
        if observed != item['mtime_ns']:
            drift.append(dict(path=item['path'], expected_mtime_ns=item['mtime_ns'], observed_mtime_ns=observed))
    return record, dict(all_file_hashes_verified=True, metadata_drift=drift)


@app.function(image=image, gpu='L4', cpu=4, memory=12288, region='us-east', i6pn=True,
              timeout=600, startup_timeout=300, retries=0, max_containers=2,
              scaledown_window=2, single_use_containers=True)
def peer(rank, exchange):
    from importlib.metadata import version
    import psutil
    sys.path.insert(0, '/opt/connector')
    import group_network
    import sglang_group
    import snapshots

    if version('sglang') != '0.5.20':
        raise ValueError('Pinned SGLang image version changed')
    try:
        address = str(ipaddress.ip_address(socket.getaddrinfo('i6pn.modal.local', None, socket.AF_INET6)[0][4][0]))
        gpu = inventory()
        source = json.loads(Path('/opt/model-snapshot.json').read_text())
        record, verification = verify_image_snapshot(snapshots, source)
        hashes = {name: hashlib.sha256((Path('/opt/connector') / name).read_bytes()).hexdigest()
                  for name in ('group_network.py', 'group_mesh.py', 'sglang_runtime.py', 'sglang_group.py', 'snapshots.py', 'group_supervisor.py', 'sglang_group_runtime.py')}
        info = dict(address=address, interface=private_interface(address), gpu=gpu, record=record,
                    source_hashes=hashes, container_hostname=socket.gethostname(), verification=verification)
        exchange.put(f'inventory-{rank}', info)
    except BaseException as error:
        exchange.put('abort', dict(rank=rank, error=type(error).__name__))
        raise
    process, children, outcome = None, {}, {}
    lifetime, engine_lock = ExitStack(), threading.Lock()
    log_path = Path(f'/tmp/sglang-rank-{rank}.log')

    def remember_children():
        if process is not None:
            try:
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    children[child.pid] = child.create_time()
            except psutil.NoSuchProcess:
                pass

    def stop_owned():
        with engine_lock:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    try:
        config = wait_value(exchange, f'config-{rank}', 120)
        current_gpu = inventory()
        if current_gpu['id'] != gpu['id']:
            raise ValueError('Assigned GPU identity changed')
        compiled = sglang_group.rank_launch(config['plan'], record, rank, [current_gpu['total_bytes']])
        topology = group_network.PeerTopology(compiled['topology'])
        if compiled['topology'] != config['topology']:
            raise ValueError('The signed peer roster does not match this compiled launch')
        directory = Path(lifetime.enter_context(tempfile.TemporaryDirectory()))
        bundle = directory / 'credentials'; bundle.mkdir()
        manifest = dict(protocol=1, rank=rank, topology=config['topology'],
            ca_sha256=hashlib.sha256(ssl.PEM_cert_to_DER_cert(config['ca'])).hexdigest())
        files = {'ca.pem': config['ca'], 'certificate.pem': config['cert'], 'key.pem': config['key'],
                 'group-peer.json': json.dumps(manifest)}
        for name, value in files.items():
            with os.fdopen(os.open(bundle / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400), 'w') as file:
                file.write(value)
        bundle.chmod(0o500)
        shutil.copyfile('/opt/connector/sglang_group_runtime.py', directory / 'entry.py')
        (directory / 'group-plan.json').write_text(json.dumps(config['plan']))
        Path('/fleet/state').mkdir(exist_ok=True)
        token = uuid.uuid4().hex
        environment = dict(os.environ, PYTHONPATH='/opt/connector',
            PANTHEON_PORT_HTTP='18409', PANTHEON_APP_RPC_TOKEN=token,
            PANTHEON_INSTANCE_ID=uuid.uuid4().hex, PANTHEON_INSTANCE_GENERATION='1',
            PANTHEON_FLEET_ID=config['plan']['owner'], PANTHEON_NODE_ID=f'acceptance-{rank}',
            PANTHEON_GROUP_CREDENTIALS=str(bundle))
        def healthy():
            if process.poll() is not None:
                return False
            try:
                request = Request('http://127.0.0.1:18409/ready', headers={'X-Pantheon-App-Token': token})
                with urlopen(request, timeout=2) as response:
                    return json.load(response) == dict(protocol=1, ready=True,
                        instance_id=environment['PANTHEON_INSTANCE_ID'], generation=1)
            except OSError:
                return False
        start = time.monotonic()
        with log_path.open('w') as log:
            process = subprocess.Popen([sys.executable, str(directory / 'entry.py'), 'start'],
                env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        exchange.put(f'state-{rank}', 'loading')
        deadline = start + 450
        while time.monotonic() < deadline:
            remember_children()
            if process.poll() is not None:
                raise RuntimeError(f'Original supervisor exited with {process.returncode}')
            if exchange.get('abort', None):
                raise RuntimeError('Original peer failed during startup')
            if healthy():
                break
            time.sleep(1)
        else:
            raise TimeoutError('Production cohort readiness deadline')
        readiness = 'ready' if rank == 0 else 'worker_ready'
        loaded_gpu = inventory()
        if loaded_gpu['used_bytes'] > (16 << 30):
            raise RuntimeError('Measured GPU usage exceeded the declared rank budget')
        outcome = dict(rank=rank, readiness=readiness, startup_ms=round((time.monotonic()-start)*1000, 1),
            gpu=gpu, loaded_gpu=loaded_gpu, source_hashes=hashes, topology=topology.fingerprint,
            private_address=address, private_interface=info['interface'], production_supervisor=True,
            engine_env=compiled['env'], container_hostname=info['container_hostname'], verification=verification)
        exchange.put(f'state-{rank}', readiness)
        if rank == 0:
            # Both local readiness values are required; a mere peer "loading"
            # observation does not qualify the complete model group.
            wait_until = time.monotonic() + 30
            while exchange.get('state-1') != 'worker_ready':
                if time.monotonic() > wait_until:
                    raise TimeoutError('Original worker did not become ready')
                time.sleep(.25)
            payload = dict(model='fleet-snapshot-'+record['sha256'],
                messages=[dict(role='user', content='Say hello in one short sentence.')],
                max_tokens=32, temperature=0)
            started = time.monotonic()
            request = Request('http://127.0.0.1:30000/v1/chat/completions',
                data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=60) as response:
                answer = json.load(response)
            assert answer['choices'][0]['message']['content'] and answer['usage']['completion_tokens'] > 0
            assert healthy()
            outcome['inference'] = dict(content=answer['choices'][0]['message']['content'],
                usage=answer['usage'], elapsed_ms=round((time.monotonic()-started)*1000, 1))
            exchange.put('finished', True)
        else:
            wait_value(exchange, 'finished', 90)
        assert healthy()
        # Failure acceptance: the only injected fault is stopping this attempt's
        # original rank1 engine AFTER successful inference. Rank0 must learn of
        # cohort loss over the live mTLS channel and stop its own engine. Neither
        # the controller nor the other rank sends a lifecycle stop to rank0.
        if rank == 0:
            fault_wait = time.monotonic()
            exchange.put('observing-fault', True)
        else:
            wait_value(exchange, 'observing-fault', 15)
            fault_wait = time.monotonic()
            original = [child for child in psutil.Process(process.pid).children()
                        if 'sglang.launch_server' in child.cmdline()]
            assert len(original) == 1, 'Expected exactly one original engine child'
            original[0].kill()  # Supervisor and the other rank receive no stop call.
        process.wait(timeout=25)
        assert not healthy(), 'Failed cohort remained ready'
        # Prove autonomous cleanup BEFORE the fixture's fallback finally block.
        deadline = time.monotonic() + 15
        def surviving_workers():
            remaining = []
            for candidate in psutil.process_iter(['pid', 'status']):
                try:
                    if candidate.info['status'] != psutil.STATUS_ZOMBIE and os.getpgid(candidate.pid) == process.pid:
                        remaining.append(candidate.pid)
                except ProcessLookupError:
                    pass
            return remaining
        while surviving_workers() and time.monotonic() < deadline:
            time.sleep(.1)
        assert not surviving_workers(), 'Production supervisor left owned workers alive'
        outcome['failure_acceptance'] = dict(injected_at_rank=1,
            readiness_withdrawn=True, original_engine_exited=True,
            fault_barrier_to_local_exit_ms=round((time.monotonic()-fault_wait)*1000, 1))
        exchange.put(f'state-{rank}', 'failure-stopped')
    except BaseException as error:
        exchange.put('abort', dict(rank=rank, error=type(error).__name__))
        if log_path.exists():
            print(f'RANK {rank} ENGINE LOG TAIL\n' + '\n'.join(log_path.read_text(errors='replace').splitlines()[-80:]), flush=True)
        raise
    finally:
        remember_children()
        stop_owned()
        lifetime.close()
        # Only exact descendants observed under our own Popen and birth times.
        for pid, born in children.items():
            try:
                child = psutil.Process(pid)
                if child.create_time() == born and child.status() != psutil.STATUS_ZOMBIE:
                    child.kill()
                    child.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        cleanup_deadline = time.monotonic()+15
        final_gpu = inventory()
        while final_gpu['used_bytes'] > gpu['used_bytes'] + (64 << 20) and time.monotonic() < cleanup_deadline:
            time.sleep(.5)
            final_gpu = inventory()
        clean = (process is None or process.poll() is not None) and final_gpu['used_bytes'] <= gpu['used_bytes'] + (64 << 20)
        cleanup = dict(process_exited=process is None or process.poll() is not None,
                       final_gpu=final_gpu, returned_to_baseline=clean)
        exchange.put(f'cleanup-{rank}', cleanup)
        outcome['cleanup'] = cleanup
        if log_path.exists():
            outcome['collective_log'] = [line for line in log_path.read_text(errors='replace').splitlines()
                if ('NCCL INFO' in line and any(word in line for word in ('Bootstrap', 'Using network', 'NET/Socket', 'Init COMPLETE')))][-20:]
        if not clean:
            raise RuntimeError('Owned rank resources did not return to baseline')
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir()
    # Reuse the already-tested ephemeral test issuer; never copy private keys to
    # receipts. This import is local only, after the container entrypoint import.
    spec = importlib.util.spec_from_file_location('peer_fixture', ROOT / 'fleet/scripts/verify-group-private-network.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    spec = importlib.util.spec_from_file_location('group_network', ROOT / 'pantheon/models/group_network.py')
    network = importlib.util.module_from_spec(spec)
    sys.modules['group_network'] = network
    spec.loader.exec_module(network)
    sys.path.insert(0, str(ROOT / 'apps/model-service'))
    import sglang_group
    source_hashes = {name: hashlib.sha256((ROOT / ('pantheon/models' if name in {'group_network.py', 'group_mesh.py'} else 'apps/model-service') / name).read_bytes()).hexdigest()
                     for name in ('group_network.py', 'group_mesh.py', 'sglang_runtime.py', 'sglang_group.py', 'snapshots.py', 'group_supervisor.py', 'sglang_group_runtime.py')}
    handle = dict(state='preparing', calls=[], image=IMAGE, model_revision=REVISION,
                  gpu='2 independent L4:1 containers', public_tunnels=0, source_hashes=source_hashes)
    def save():
        temporary = output / 'handle.tmp'
        temporary.write_text(json.dumps(handle, indent=2)+'\n')
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
                info = [wait_value(exchange, f'inventory-{rank}', 300) for rank in range(2)]
                (output / 'verification.json').write_text(json.dumps([row['verification'] for row in info], indent=2)+'\n')
                assert info[0]['address'] != info[1]['address'] and info[0]['gpu']['id'] != info[1]['gpu']['id']
                assert info[0]['record'] == info[1]['record']
                assert all(row['source_hashes'] == source_hashes for row in info)
                plan = dict(protocol=1, owner='f_'+uuid.uuid4().hex[:16], group_id='sglang-acceptance-'+uuid.uuid4().hex[:12],
                    recipe_id='sglang-0.5.20-linux-amd64', model_sha256=info[0]['record']['sha256'],
                    tensor_parallel_size=2, context_length=2048, parallel=1, rendezvous_port=18408,
                    members=[dict(rank=rank, node_id=f'acceptance-{rank}', generation=1,
                        address=row['address'], control_port=18407, interface=row['interface'],
                        physical_gpu_bytes=[row['gpu']['total_bytes']], resources=dict(memory_bytes=12 << 30,
                            devices=[dict(id=row['gpu']['id'], backend='cuda', memory_bytes=16 << 30, exclusive=True)]))
                        for rank, row in enumerate(info)])
                compiled = sglang_group.rank_launch(plan, info[0]['record'], 0, [info[0]['gpu']['total_bytes']])
                topology = network.PeerTopology(compiled['topology'])
                for rank, certificate in enumerate(fixture.certificates(topology)):
                    exchange.put(f'config-{rank}', {**certificate, 'plan': plan})
                (output / 'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
                handle['state'] = 'running'
                save()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(call.get, timeout=550) for call in calls]
                    while not all(future.done() for future in futures):
                        print(json.dumps(dict(states=[exchange.get(f'state-{rank}', 'preflight') for rank in range(2)],
                            abort=exchange.get('abort', None))), flush=True)
                        time.sleep(10)
                    results = [future.result() for future in futures]
                result = dict(passed=True, plan=plan, ranks=results,
                    scope='Production supervisor/entrypoint: cross-container SGLang TP2 inference and original-engine failure cleanup; Fleet credential delivery, physical host placement and installed Fleet/Docker lifecycle unverified')
                (output / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
                print(json.dumps(result), flush=True)
            finally:
                cleanup = {str(rank): exchange.get(f'cleanup-{rank}', None) for rank in range(2)}
                (output / 'cleanup.json').write_text(json.dumps(cleanup, indent=2)+'\n')
                errors = []
                for call in calls:
                    try: call.cancel(terminate_containers=True)
                    except Exception as error: errors.append(type(error).__name__)
                handle['cleanup_errors'] = errors
                handle['state'] = 'cleanup-pending' if errors else 'calls-cancelled'
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
