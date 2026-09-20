"""Real Browser -> owned OS window -> authenticated stream -> Agent read.

Uses a temporary profile and a synthetic data URL, never an existing browser.
Run only in an interactive desktop with recording/input permission granted.
"""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

from aiohttp import ClientSession

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pantheon.apps.native_stream import Runtime


async def main():
    os.environ['PANTHEON_NATIVE_CAPTURE_HELPER'] = str(Path(sys.argv[1]).resolve())
    os.environ['PANTHEON_PORT_STREAM'] = '0'
    with tempfile.TemporaryDirectory(prefix='fleet-browser-test-') as directory:
        root = Path(directory)
        runtime = Runtime(SimpleNamespace(app_id='browser', workspace=root, state_dir=root))
        # execution_package copies this exact module into .stream-runtime.
        name = 'pantheon.apps.native_stream.browser_snapshot'
        spec = importlib.util.spec_from_file_location(name, ROOT / 'apps/desktop/browser_snapshot.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        session = None
        try:
            await runtime.start()
            session = await runtime.launch('fixture', 'data:text/html,<title>Fleet Browser test</title><h1>Owned browser capture</h1>')
            info = await runtime.info(session)
            assert info['title'] == 'Fleet Browser test', info
            assert session.frames, 'No native Browser frame'
            read = await runtime.dispatch('browser_read', {'page_id': session.id})
            assert 'Owned browser capture' in str(read), read
            assert 'password' not in read
            stage = await runtime.dispatch('browser_ui_stage', {'page_id': session.id, 'width': 640, 'height': 480})
            assert stage['stream_protocol'] == 'native-v1'
            port = runtime.runner.addresses[0][1]
            async with ClientSession() as client:
                async with client.ws_connect(f'http://127.0.0.1:{port}/native-stream') as ws:
                    await ws.send_json({'password': stage['password']})
                    assert (await ws.receive_json())['event'] == 'ready'
                    frame = await ws.receive_bytes(timeout=10)
                    assert frame[4:6] == b'\xff\xd8'
            print('PASS Chromium launch, first frame, Agent DOM read, stage and authenticated JPEG stream', flush=True)
        finally:
            if session and session.browser:
                # This profile contains only the synthetic fixture.
                await session.browser.close()
            if session and session.process.poll() is None:
                session.process.terminate()
                await asyncio.to_thread(session.process.wait)
            await runtime.cleanup()


asyncio.run(main())
