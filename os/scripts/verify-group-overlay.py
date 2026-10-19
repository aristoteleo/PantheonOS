#!/usr/bin/env python3
"""Opt-in kernel WireGuard acceptance in one disposable privileged container.

Requires a running local Linux Docker engine and a cached python:3.12-slim image
(or --image). Does not pull images, configure host routes, or touch Fleet nodes.
Test dependencies are installed only in the disposable container.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='python:3.12-slim')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fleet = Path(__file__).resolve().parents[1]
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True))[0]
    architecture = image['Architecture']
    if architecture not in {'amd64', 'arm64'} or image['Os'] != 'linux':
        raise SystemExit('Use a cached Linux amd64/arm64 Python image')
    name = 'pantheon-group-overlay-' + uuid.uuid4().hex[:16]
    receipt = dict(image_id=image['Id'], architecture=architecture, container=name,
                   scope='single Linux kernel; namespace isolation, not multi-host GPU acceptance')
    try:
        with tempfile.TemporaryDirectory(prefix='fleet-group-overlay-') as temporary:
            binary = Path(temporary) / 'network.test'
            env = dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH=architecture)
            subprocess.run(['go', 'test', '-p', '2', '-c', '-o', str(binary), './internal/groupnetwork'],
                           cwd=fleet, env=env, check=True, timeout=120)
            command = ['docker', 'run', '--pull', 'never', '--name', name,
                '--cpus', '2', '--memory', '512m', '--pids-limit', '128', '--privileged',
                '--mount', f'type=bind,src={binary},dst=/network.test,readonly',
                '--env', 'DEBIAN_FRONTEND=noninteractive',
                '--env', 'PANTHEON_GROUP_NETWORK_TEST=isolated-container', image['Id'],
                'sh', '-c', 'apt-get update -qq && apt-get install -y --no-install-recommends '
                'iproute2 wireguard-tools >/tmp/packages.log && '
                'dpkg-query -W iproute2 wireguard-tools && uname -r && '
                '/network.test -test.v -test.run TestKernelEncryptedNamespace']
            with (args.output / 'kernel.log').open('w') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            receipt['exit_code'] = result.returncode
            result.check_returncode()
    finally:
        # Only this randomly named acceptance container is eligible for cleanup.
        result = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=30)
        receipt['container_removed'] = result.returncode == 0 or 'No such container' in result.stderr
        (args.output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
