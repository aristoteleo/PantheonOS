"""Opt-in Linux CPU process-group acceptance; no GPU or installed Fleet changes.

Uses the production LinuxEngine with real child/grandchild processes. The abrupt
death case verifies that the group remains targetable by the original Fleet
process-group identity; this fixture then performs that group cleanup. It does
not claim an actual installed Runner or full distributed GPU acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path

import modal

app = modal.App('fleet-group-supervisor-linux-acceptance')
image = modal.Image.debian_slim(python_version='3.12')
if modal.is_local():
    ROOT = Path(__file__).resolve().parents[2]
    for source in ('apps/model-service/group_supervisor.py', 'pantheon/models/group_network.py'):
        image = image.add_local_file(ROOT / source, '/opt/group/' + Path(source).name, copy=True)


@app.function(image=image, cpu=.5, memory=512, timeout=90, startup_timeout=120,
              retries=0, max_containers=1, scaledown_window=2, single_use_containers=True)
def verify():
    import os
    import signal
    import socket
    import subprocess
    import sys
    import tempfile
    import time

    def members(group):
        result = []
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                row = path.read_text().rpartition(') ')[2].split()
                if int(row[2]) == group and row[0] not in {'Z', 'X'}:
                    result.append(int(path.parent.name))
            except (FileNotFoundError, ProcessLookupError):
                pass
        return result

    supervisor = '''import os,sys,signal,threading,time,json
from pathlib import Path
sys.path.insert(0,'/opt/group')
from group_supervisor import LinuxEngine
stop=threading.Event()
signal.signal(signal.SIGTERM,lambda *_:stop.set())
signal.signal(signal.SIGINT,lambda *_:stop.set())
directory,mode,foreign_port=sys.argv[1:]
child="""import os,sys,signal,subprocess,time,json,socket
from pathlib import Path
directory,mode=sys.argv[1:]
stubborn=mode=='stubborn'
if stubborn:signal.signal(signal.SIGTERM,signal.SIG_IGN)
grandchild=subprocess.Popen([sys.executable,'-c',"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN) if "+repr(stubborn)+" else None; time.sleep(60)"])
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
evidence=Path(directory,'workers.tmp')
evidence.write_text(json.dumps(dict(engine=os.getpid(),grandchild=grandchild.pid,group=os.getpgrp(),port=listener.getsockname()[1])))
evidence.replace(Path(directory,'workers.json'))
if mode=='orphan':sys.exit(0)
time.sleep(60)
"""
engine=LinuxEngine([sys.executable,'-c',child,directory,mode],dict(os.environ),stop_timeout=.4)
engine.start()
if mode=='cooperative':
 deadline=time.monotonic()+5
 while not Path(directory,'workers.json').exists():
  assert time.monotonic()<deadline
  time.sleep(.01)
 port=json.loads(Path(directory,'workers.json').read_text())['port']
 identity=engine.listener_identity(port)
 assert identity and engine.listener_identity(port)==identity
 assert engine.listener_identity(int(foreign_port)) is None
 Path(directory,'listeners-checked').write_text('original accepted; foreign rejected')
while not stop.wait(.02):
 if engine.poll() is not None:break
engine.stop()
Path(directory,'stopped').write_text('confirmed')
'''
    results = []
    foreign = socket.socket()
    foreign.bind(('127.0.0.1', 0)); foreign.listen()
    untouched = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(90)'], start_new_session=True)
    try:
        for mode in ('cooperative', 'stubborn', 'orphan', 'abrupt-supervisor'):
            with tempfile.TemporaryDirectory() as directory:
                process = subprocess.Popen([sys.executable, '-c', supervisor, directory, mode, str(foreign.getsockname()[1])], start_new_session=True)
                try:
                    deadline = time.monotonic() + 8
                    evidence = Path(directory, 'workers.json')
                    while not evidence.exists():
                        if time.monotonic() > deadline or process.poll() is not None:
                            raise RuntimeError('Owned process fixture did not start')
                        time.sleep(.02)
                    workers = json.loads(evidence.read_text())
                    assert workers['group'] == process.pid
                    if mode == 'cooperative':
                        while not Path(directory, 'listeners-checked').exists():
                            if time.monotonic() > deadline or process.poll() is not None:
                                raise RuntimeError('Listener ownership checks failed')
                            time.sleep(.02)
                    started = time.monotonic()
                    if mode == 'abrupt-supervisor':
                        process.kill()
                        process.wait(timeout=3)
                        assert workers['engine'] in members(process.pid)
                        # Exact original process group, as used by Fleet native stop.
                        os.killpg(process.pid, signal.SIGTERM)
                    elif mode != 'orphan':
                        process.terminate()
                    process.wait(timeout=5)
                    while members(process.pid) and time.monotonic() - started < 4:
                        time.sleep(.02)
                    assert not members(process.pid), 'original worker group survived'
                    assert untouched.poll() is None, 'unrelated process was affected'
                    if mode == 'stubborn':
                        assert process.returncode == -signal.SIGKILL
                        assert not Path(directory, 'stopped').exists()
                    elif mode != 'abrupt-supervisor':
                        assert process.returncode == 0 and Path(directory, 'stopped').exists()
                    results.append(dict(scenario=mode, passed=True, group_released=True,
                        listener_ownership_checked=mode == 'cooperative',
                        supervisor_returncode=process.returncode, unrelated_process_alive=True,
                        stop_ms=round((time.monotonic()-started)*1000,1)))
                finally:
                    if members(process.pid):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
    finally:
        foreign.close()
        untouched.terminate(); untouched.wait(timeout=5)
    return dict(passed=True, gpu=False, source_sha256=hashlib.sha256(Path('/opt/group/group_supervisor.py').read_bytes()).hexdigest(), scenarios=results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    handle = dict(state='starting', gpu=False, calls=[])
    def save():
        temp = args.output / 'handle.tmp'
        temp.write_text(json.dumps(handle, indent=2) + '\n')
        temp.replace(args.output / 'handle.json')
    save()
    try:
        with modal.enable_output(), app.run():
            handle['app_id'] = app.app_id
            call = verify.spawn()
            handle['calls'].append(call.object_id)
            handle['state'] = 'running'; save()
            try:
                result = call.get(timeout=120)
                assert result['source_sha256'] == hashlib.sha256((ROOT / 'apps/model-service/group_supervisor.py').read_bytes()).hexdigest()
                (args.output / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
                print(json.dumps(result), flush=True)
            finally:
                handle['state'] = 'cleanup-pending'; save()
                call.cancel(terminate_containers=True)
                handle['state'] = 'calls-cancelled'; save()
        handle['state'] = 'app-stopped'; save()
    except BaseException:
        handle['failed'] = True; save()
        raise


if __name__ == '__main__':
    main()
