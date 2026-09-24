#!/usr/bin/env python3
"""Opt-in actual Fleet Manager/Docker group-network lifecycle acceptance.

The disposable administrative controller uses host PID visibility and the local
Docker socket to exercise NativeDriver. Its node state is a dedicated bind mount
so Docker's sibling workloads see the same paths. It never deploys a Fleet node,
pulls model weights or uses GPUs. Requires cached Python and hello-world images.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def inspect(value):
    return json.loads(subprocess.check_output(['docker', 'inspect', value], text=True))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = output / 'state'
    state.mkdir(mode=0o700)  # Never reuse a previous run's node state.
    image = inspect('python:3.12-slim')
    pinned = next(ref for ref in image['RepoDigests'] if ref.startswith('python@sha256:'))
    name = 'pf-lifecycle-accept-' + uuid.uuid4().hex[:16]
    receipt = dict(image=image['Id'], image_reference=pinned, controller=name,
                   scope='actual Fleet lifecycle, CPU containers on one Docker Linux host; no GPU acceptance')
    binary = args.binary.resolve() if args.binary else output / 'lifecycle.test'
    try:
        if not args.binary:
            subprocess.run(['go', 'test', '-p', '2', '-c', '-o', str(binary), './internal/lifecycle'],
                cwd=Path(__file__).resolve().parents[1], check=True, timeout=180,
                env=dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH=image['Architecture']))
        command = ['docker', 'run', '--pull', 'never', '--name', name, '--privileged', '--pid', 'host',
            '--cpus', '2', '--memory', '768m', '--pids-limit', '192',
            '--mount', f'type=bind,src={binary},dst=/lifecycle.test,readonly',
            '--mount', f'type=bind,src={state},dst={state}',
            '--mount', 'type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
            '-e', 'DEBIAN_FRONTEND=noninteractive', '-e', f'TMPDIR={state}',
            '-e', 'PANTHEON_GROUP_LIFECYCLE_TEST=isolated-container', '-e', f'PANTHEON_GROUP_TEST_ROOT={state}',
            '-e', f'PANTHEON_GROUP_TEST_IMAGE={pinned}', image['Id'], 'sh', '-c',
            'apt-get update -qq && apt-get install -y --no-install-recommends iproute2 wireguard-tools docker-cli '
            '>/tmp/packages.log && dpkg-query -W iproute2 wireguard-tools docker-cli && '
            '/lifecycle.test -test.v -test.timeout 120s -test.run TestGroupContainerNativeLifecycle']
        with (output / 'lifecycle.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=240)
        receipt['exit_code'] = result.returncode
        result.check_returncode()
    finally:
        result = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=30)
        receipt['controller_removed'] = result.returncode == 0 or 'No such container' in result.stderr
        receipt['workload_cleanup'] = {}
        owned = state / 'owned-containers.json'
        for resource in json.loads(owned.read_text()) if owned.exists() else []:
            check = subprocess.run(['docker', 'container', 'inspect', resource], capture_output=True, text=True, timeout=15)
            if check.returncode != 0:
                receipt['workload_cleanup'][resource] = 'already removed' if ('No such object: ' + resource in check.stderr or 'No such container: ' + resource in check.stderr) else 'inspection uncertain'
                continue
            original = json.loads(check.stdout)[0]
            if original['Config']['Labels'].get('pantheon.resource') != resource:
                receipt['workload_cleanup'][resource] = 'ownership mismatch; retained'
                continue
            result = subprocess.run(['docker', 'rm', '-f', original['Id']], capture_output=True, text=True, timeout=30)
            receipt['workload_cleanup'][resource] = 'removed by harness after failure' if result.returncode == 0 else 'removal failed'
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
