"""Real Fleet-supervised SGLang inference on an isolated, bounded Modal L4.

No production node registration, user volume, credentials or external API calls.
Image and public small model are immutable; image/model preparation uses CPU.
The GPU allocation is created only after preparation and is always terminated.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import modal

IMAGE = 'lmsysorg/sglang@sha256:4bf342cb756a7105e6df9ae81abeb62e891ff70d34b83fdd7a891fa46a494eca'  # v0.5.20-runtime linux/amd64
MODEL = 'Qwen/Qwen2.5-0.5B-Instruct'
REVISION = '7ae557604adf67be50417f59c2c2f167def9a775'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tp', type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    handle = Path(f'/tmp/model-services-sglang-tp{args.tp}-sandbox.json')
    with tempfile.TemporaryDirectory(prefix='fleet-sglang-check-') as directory:
        binary = Path(directory) / 'lifecycle.test'
        subprocess.run(['go', 'test', '-p', '2', '-c', './internal/lifecycle', '-o', str(binary)], cwd=root,
                       env={**os.environ, 'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '0'}, check=True)
        image = (modal.Image.from_registry(IMAGE)
                 .run_commands(f"python3 -c \"from huggingface_hub import snapshot_download; snapshot_download('{MODEL}', revision='{REVISION}', local_dir='/opt/model', ignore_patterns=['*.bin', '*.h5', '*.msgpack'])\"")
                 .add_local_dir(root.parent / 'apps' / 'model-service', '/opt/connector', copy=True, ignore=['__pycache__'])
                 .add_local_file(root / 'scripts' / 'prepare-sglang-fixture.py', '/opt/prepare-sglang-fixture.py', copy=True)
                 .run_commands('python3 /opt/prepare-sglang-fixture.py')
                 .add_local_file(binary, '/opt/lifecycle.test', copy=True))
        app = modal.App.lookup('fleet-model-services-acceptance', create_if_missing=True)
        with modal.enable_output():
            sandbox = modal.Sandbox.create(app=app, image=image, gpu=f'L4:{args.tp}', cpu=4,
                                           memory=(24576, 24576), timeout=600, idle_timeout=60)
        handle.write_text(json.dumps({'sandbox_id': sandbox.object_id, 'state': 'running', 'image': IMAGE, 'model_revision': REVISION, 'tensor_parallel_size': args.tp}))
        print('SGLang sandbox', sandbox.object_id, flush=True)
        try:
            process = sandbox.exec('/opt/lifecycle.test', '-test.v', '-test.run', '^TestLiveSGLangManagedInference$', '-test.timeout', '540s',
                                   env={'FLEET_TEST_SGLANG': '1', 'FLEET_TEST_SGLANG_TP': str(args.tp)}, timeout=550)
            for line in process.stdout:
                print(line, end='', flush=True)
            print(process.stderr.read(), end='', flush=True)
            process.wait()
            if process.returncode != 0:
                raise RuntimeError(f'GPU inference acceptance failed: {process.returncode}')
        finally:
            sandbox.terminate()
            handle.write_text(json.dumps({'sandbox_id': sandbox.object_id, 'state': 'terminated', 'image': IMAGE, 'model_revision': REVISION, 'tensor_parallel_size': args.tp}))
            print('SGLang sandbox terminated', flush=True)


if __name__ == '__main__':
    main()
