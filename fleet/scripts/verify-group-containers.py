#!/usr/bin/env python3
"""Verify Fleet overlay admission into two actual, unprivileged Docker containers.

Uses a cached Python image; no GPU/model download. Only a disposable controller
test container has host PID visibility and network administration capabilities.
It has its own network and mount namespaces and no Docker socket. Workload
containers have --network none and no capabilities. This is a single-host kernel
acceptance, not distributed GPU inference or installed Fleet lifecycle acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


SERVER = '''import os,signal,socket,time
signal.signal(signal.SIGTERM,lambda *_:exit(0))
s=socket.socket();s.settimeout(1)
deadline=time.monotonic()+150
while True:
 try:s.bind((os.environ['TEST_ADDRESS'],32123));break
 except OSError:
  if time.monotonic()>deadline:raise
  time.sleep(.05)
s.listen(4)
while True:
 try:c,_=s.accept()
 except TimeoutError:continue
 with c:
  c.settimeout(5);c.sendall(c.recv(128))
'''


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='python:3.12-slim')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    image = inspect(args.image)
    if image['Os'] != 'linux' or image['Architecture'] not in {'arm64', 'amd64'}:
        raise ValueError('Use a cached Linux image')
    token = uuid.uuid4().hex
    names = ['pf-net-accept-' + token[:12] + '-' + suffix for suffix in ('rank0', 'rank1', 'controller')]
    receipt = dict(image=image['Id'], architecture=image['Architecture'], containers=names,
                   scope='two Docker containers, one Linux host; not GPU or installed lifecycle')
    try:
        pids = []
        receipt['workloads'] = []
        for rank, name in enumerate(names[:2]):
            subprocess.run(['docker', 'run', '--pull', 'never', '-d', '--name', name,
                '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '--user', '65534:65534', '--memory', '64m', '--cpus', '.25', '--pids-limit', '16',
                '-e', 'PANTHEON_GROUP_TEST_TOKEN=' + token, '-e', f'TEST_ADDRESS=10.251.1.{rank+1}',
                image['Id'], 'python3', '-u', '-c', SERVER], check=True, capture_output=True, timeout=30)
            state = inspect(name)
            if not state['State']['Running'] or state['HostConfig']['NetworkMode'] != 'none':
                raise ValueError('Workload did not enter isolated wait')
            pids.append(str(state['State']['Pid']))
            receipt['workloads'].append(dict(id=state['Id'], pid=state['State']['Pid']))
        with tempfile.TemporaryDirectory(prefix='fleet-container-network-') as directory:
            binary = Path(directory) / 'network.test'
            env = dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH=image['Architecture'])
            subprocess.run(['go', 'test', '-p', '2', '-c', '-o', str(binary), './internal/groupnetwork'],
                cwd=Path(__file__).resolve().parents[1], env=env, check=True, timeout=120)
            command = ['docker', 'run', '--pull', 'never', '--name', names[2],
                '--pid', 'host', '--privileged', '--cpus', '2', '--memory', '512m', '--pids-limit', '128',
                '--mount', f'type=bind,src={binary},dst=/network.test,readonly',
                '-e', 'DEBIAN_FRONTEND=noninteractive', '-e', 'PANTHEON_GROUP_NETWORK_TEST=isolated-container',
                '-e', 'PANTHEON_GROUP_TEST_PIDS=' + ','.join(pids), '-e', 'PANTHEON_GROUP_TEST_TOKEN=' + token,
                image['Id'], 'sh', '-c', 'apt-get update -qq && apt-get install -y --no-install-recommends '
                'iproute2 wireguard-tools >/tmp/packages.log && '
                'dpkg-query -W iproute2 wireguard-tools && uname -r && '
                '/network.test -test.v -test.run TestKernelAttachedContainers']
            with (args.output / 'kernel.log').open('w') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            receipt['exit_code'] = result.returncode
            receipt['workload_exit_states'] = [inspect(name)['State'] for name in names[:2]]
            result.check_returncode()
            if any(state['Running'] or state['ExitCode'] != 0 for state in receipt['workload_exit_states']):
                raise ValueError('Test did not stop both original workloads cleanly')
    finally:
        receipt['removed'] = {}
        for name in reversed(names):
            result = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=30)
            receipt['removed'][name] = result.returncode == 0 or 'No such container' in result.stderr
        (args.output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
