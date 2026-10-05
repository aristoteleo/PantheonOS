"""Live acceptance of the packaged Evolution Tools App on one CPU Modal sandbox.

This uses real Files, Python kernels, Shell and evaluator subprocesses. It does
not invoke a model or the Evolution controller. The durable receipt directory
must be new and is retained on failure for reconciliation; do not reuse it to
launch another container. The build sends only the ordinary App artifact.
"""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.modal_image import build_modal_image
from pantheon.apps.modal_sandbox import ModalSandboxOwner
from pantheon.apps.modal_app_transport import ModalAppTransport
from pantheon.evolution.sandbox.package import build_package


EVALUATOR = "def evaluate(path):\n from pathlib import Path\n return {'score':int(Path(path,'main.py').read_text().split('=')[1])/10,'fitness_weights':{'score':1}}"


async def main(root):
    import modal
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    package = build_package(root / 'package')
    _, digest = build_artifact(package)
    app_name = 'pantheon-agent-extraction-acceptance'
    app = await modal.App.lookup.aio(app_name, create_if_missing=True)
    base = modal.Image.debian_slim(python_version='3.12')
    print('Preparing the pinned tool image and its locked dependencies', flush=True)
    await base.build.aio(app)
    start = time.monotonic()
    image = await build_modal_image(package, artifact_sha256=digest,
        base_image_id=base.object_id, app_name=app_name)
    (root / 'image.json').write_text(json.dumps(asdict(image), indent=2))
    print(json.dumps({**asdict(image), 'build_seconds': round(time.monotonic() - start, 3)}), flush=True)
    owner = ModalSandboxOwner(root / 'container', operation_id='mutation-tools-live',
        app_name=app_name, image_id=image.image_id, argv=image.argv(), timeout=180, cpu=1, memory=1024)
    pipe = None
    try:
        start = time.monotonic()
        sandbox = await owner.start()
        print(json.dumps({'backend_id': sandbox.object_id, 'phase': 'created'}), flush=True)
        pipe = ModalAppTransport(sandbox)
        await pipe.ready(timeout=60)
        print(json.dumps({'ready_seconds': round(time.monotonic() - start, 3)}), flush=True)
        async with asyncio.timeout(90):
            await pipe.invoke('initialize', {'parent_files': {'main.py': 'x=1'},
                'evaluator_code': EVALUATOR, 'objective': 'Improve score', 'timeout': 90})
            initial = await pipe.invoke('evaluate_initial', {})
            assert initial['metrics']['score'] == .1, initial
            async def tool(provider, name, **args):
                return await pipe.invoke('invoke_tool', {'provider': provider, 'name': name, 'args': args})
            file = await tool('files', 'read_file', file_path='main.py')
            assert 'x=1' in str(file) and file.get('success') is not False, file
            python = await tool('python', 'run_python_code', code='import numpy as np\nprint(int(np.array([6]) @ np.array([7])))')
            assert '42' in str(python), python
            shell = await tool('shell', 'run_command', command="printf 'x=9' > main.py")
            assert shell.get('success') is True and shell.get('status') == 'completed', shell
            result = await pipe.invoke('finish', {})
            assert result['submitted'] and result['child_files'] == {'main.py': 'x=9'}, result
            assert result['metrics']['score'] == .9, result
            (root / 'result.json').write_text(json.dumps(result, indent=2))
            print(json.dumps({'initial_score': .1, 'final_score': .9,
                'tools': ['files', 'python', 'shell', 'evaluator'], 'phase': 'verified'}), flush=True)
        await pipe.shutdown()
        async with asyncio.timeout(30):
            await sandbox.wait.aio(raise_on_termination=False)
        assert await sandbox.poll.aio() == 0
    finally:
        receipt = await owner.stop()
        if pipe is not None:
            await pipe.disconnect()
            (root / 'app-stderr-tail.log').write_bytes(pipe.stderr_tail)
        print(json.dumps({'termination': receipt, 'receipt_dir': str(root)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-dir', type=Path, required=True)
    asyncio.run(main(parser.parse_args().receipt_dir))
