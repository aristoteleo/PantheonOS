"""Opt-in real NVIDIA acceptance in an isolated, short-lived Modal Sandbox.

Run with a Python environment containing modal and existing credentials. This
does not join a production Fleet, mount user volumes or load application secrets.
Linux test binaries are built from the current checkout, not downloaded.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import modal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', default='T4', choices=['T4', 'L4', 'A10G'])
    parser.add_argument('--handle', type=Path, default=Path('/tmp/model-services-resource-sandbox.json'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='fleet-resource-check-') as directory:
        image = modal.Image.debian_slim(python_version='3.12')
        for package in ('node', 'lifecycle'):
            binary = Path(directory) / f'{package}.test'
            subprocess.run(['go', 'test', '-c', f'./internal/{package}', '-o', str(binary)],
                           cwd=root, env={**os.environ, 'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '0'}, check=True)
            image = image.add_local_file(binary, f'/opt/{package}.test', copy=True)
        app = modal.App.lookup('fleet-model-services-acceptance', create_if_missing=True)
        with modal.enable_output():
            sandbox = modal.Sandbox.create(app=app, image=image, gpu=args.gpu, memory=(8192, 8192),
                                           cpu=2, timeout=300, idle_timeout=60)
        args.handle.write_text(json.dumps({'sandbox_id': sandbox.object_id, 'state': 'running'}))
        print(f'Sandbox {sandbox.object_id}', flush=True)
        try:
            for package, pattern in (
                ('node', 'TestLiveResourceInventory'),
                ('lifecycle', 'TestLiveResourceAdmission|TestConcurrentResourceReservations|TestReservationSurvivesRestart|TestStaticResourceBudget'),
            ):
                process = sandbox.exec(f'/opt/{package}.test', '-test.v', '-test.run', pattern,
                                       env={'FLEET_TEST_RESOURCE_INVENTORY': '1', 'FLEET_EXPECT_MEMORY_BYTES': str(8192 << 20)}, timeout=60)
                for line in process.stdout:
                    print(line, end='', flush=True)
                print(process.stderr.read(), end='', flush=True)
                process.wait()
                if process.returncode != 0:
                    raise RuntimeError(f'{package} acceptance failed: exit {process.returncode}')
        finally:
            sandbox.terminate()
            args.handle.write_text(json.dumps({'sandbox_id': sandbox.object_id, 'state': 'terminated'}))
            print('Sandbox terminated', flush=True)


if __name__ == '__main__':
    main()
