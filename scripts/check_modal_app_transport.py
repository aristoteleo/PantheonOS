"""Explicit live CPU smoke test; creates and terminates one bounded Modal App.

Run with the project Python and Modal credentials configured on the controller:
    python scripts/check_modal_app_transport.py --receipt-dir /private/path

The private receipt directory must be new. Keep it if a stop is unconfirmed;
never rerun creation against an uncertain receipt. Only the ordinary stdlib App
host and this fixture enter the image. No project files or credentials are sent.
This validates real Modal transport/stop, not the complete Evolution product.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pantheon.apps.modal_sandbox import ModalSandboxOwner
from pantheon.apps.modal_app_transport import ModalAppTransport


BACKEND = '''
import asyncio
def register(ctx):
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    @ctx.method
    async def exercise(text):
        ctx.log('remote tool entered')
        path = ctx.workspace / 'source.txt'
        path.write_text(text)
        proc = await asyncio.create_subprocess_exec('python', '-c', 'print(6*7)', stdout=asyncio.subprocess.PIPE)
        data, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError('child failed')
        url = await ctx.serve(str(path))
        return {'size': len(path.read_text()), 'calculation': data.decode().strip(), 'artifact': url}
    @ctx.on_cleanup
    async def cleanup():
        ctx.state.set('cleaned', True)
'''


async def main(receipt_dir):
    import modal
    if receipt_dir.exists():
        raise ValueError('Use a new private receipt directory; retain existing recovery records')
    timings, begin = {}, time.monotonic()
    app_name = 'pantheon-agent-extraction-acceptance'
    app = await modal.App.lookup.aio(app_name, create_if_missing=True)
    print('Building a pinned stdlib-only App image', flush=True)
    with tempfile.TemporaryDirectory(prefix='pantheon-modal-smoke-') as directory:
        fixture = Path(directory)
        (fixture / 'app.json').write_text('{}')
        (fixture / 'backend.py').write_text(BACKEND)
        image = modal.Image.debian_slim(python_version='3.12')
        image = image.add_local_file(ROOT / 'apps/desktop/app_runtime.py', '/opt/app_runtime.py', copy=True)
        image = image.add_local_file(fixture / 'app.json', '/opt/app/app.json', copy=True)
        image = image.add_local_file(fixture / 'backend.py', '/opt/app/backend/__init__.py', copy=True)
        await image.build.aio(app)
    timings['image_seconds'] = round(time.monotonic() - begin, 3)
    print(json.dumps({'image_id': image.object_id, **timings}), flush=True)
    owner = ModalSandboxOwner(receipt_dir, operation_id='ordinary-app-stdio-smoke',
        app_name=app_name, image_id=image.object_id, argv=['python', '-u', '/opt/app_runtime.py',
        '--app-dir', '/opt/app', '--app-id', 'transport-fixture', '--workspace', '/workspace', '--state-dir', '/state'],
        timeout=120, cpu=1, memory=256)
    pipe = None
    try:
        start = time.monotonic()
        sandbox = await owner.start()
        print(json.dumps({'backend_id': sandbox.object_id, 'phase': 'created'}), flush=True)
        async def callback(method, args):
            if method != 'ctx.serve' or args != {'path': '/workspace/source.txt'}:
                raise PermissionError('Unexpected callback')
            return {'url': 'smoke://scoped-artifact'}
        pipe = ModalAppTransport(sandbox, callback=callback)
        await pipe.ready(timeout=60)
        timings['create_to_ready_seconds'] = round(time.monotonic() - start, 3)
        start = time.monotonic()
        result = await asyncio.wait_for(pipe.invoke('exercise', {'text': 'code' * 60000}), 30)
        assert result == {'size': 240000, 'calculation': '42', 'artifact': 'smoke://scoped-artifact'}, result
        timings['tool_roundtrip_seconds'] = round(time.monotonic() - start, 3)
        await pipe.shutdown()
        async with asyncio.timeout(30):
            await sandbox.wait.aio(raise_on_termination=False)
        code = await sandbox.poll.aio()
        assert code == 0, f'Ordinary App shutdown exit code: {code}'
        print(json.dumps({'result': result, 'exit_code': code, **timings}), flush=True)
    finally:
        # Stop confirmation precedes releasing local IO and its owner receipt.
        receipt = await owner.stop()
        if pipe is not None:
            await pipe.disconnect()
        print(json.dumps({'termination': receipt, 'receipt_dir': str(receipt_dir)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-dir', required=True, type=Path)
    asyncio.run(main(parser.parse_args().receipt_dir))
