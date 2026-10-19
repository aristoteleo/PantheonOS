"""Bounded Linux process-tree acceptance. No GPU or production node is used."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import modal


def main():
    root = Path(__file__).resolve().parents[1]
    handle = Path('/tmp/model-services-process-sandbox.json')
    with tempfile.TemporaryDirectory(prefix='fleet-process-check-') as directory:
        binary = Path(directory) / 'lifecycle.test'
        subprocess.run(['go', 'test', '-c', './internal/lifecycle', '-o', str(binary)], cwd=root,
                       env={**os.environ, 'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '0'}, check=True)
        image = modal.Image.debian_slim(python_version='3.12').add_local_file(binary, '/opt/lifecycle.test', copy=True)
        app = modal.App.lookup('fleet-model-services-acceptance', create_if_missing=True)
        with modal.enable_output():
            sandbox = modal.Sandbox.create(app=app, image=image, cpu=1, memory=(1024, 1024), timeout=90, idle_timeout=30)
        handle.write_text(json.dumps({'sandbox_id': sandbox.object_id, 'state': 'running'}))
        try:
            process = sandbox.exec('/opt/lifecycle.test', '-test.v', '-test.run',
                                   '^TestStopWaitsForOwnedWorkerAfterLeaderExits$', '-test.timeout', '30s', timeout=40)
            for line in process.stdout:
                print(line, end='', flush=True)
            print(process.stderr.read(), end='', flush=True)
            process.wait()
            if process.returncode:
                raise RuntimeError('Linux owned-worker drain acceptance failed')
        finally:
            sandbox.terminate()
            handle.write_text(json.dumps({'sandbox_id': sandbox.object_id, 'state': 'terminated'}))
            print('CPU sandbox terminated', flush=True)


if __name__ == '__main__':
    main()
