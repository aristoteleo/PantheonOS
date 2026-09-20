"""Real Chromium/Pion loopback test. Only generated pixels, no screen access.
Usage: peer_smoke.py FLEET_BINARY VIEWER_ESM [ENCODER_PACKETS]
The ESM is bundled from nativeCapture.ts with esbuild. Optional packets come
from the Mac helper's --test-encoder (synthetic VideoToolbox H264 frames).
"""
import asyncio
import contextlib
import io
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from aiohttp import web
from PIL import Image
from playwright.async_api import async_playwright
from pantheon.apps.native_stream import Runtime, Session


async def main():
    os.environ['PANTHEON_FLEET_EXECUTABLE'] = str(Path(sys.argv[1]).resolve())
    bundle = Path(sys.argv[2]).read_bytes()
    video = []
    if len(sys.argv) > 3:
        packets = Path(sys.argv[3]).read_bytes()
        while packets:
            size, = struct.unpack('>I', packets[:4])
            packet, packets = packets[4:4+size], packets[4+size:]
            assert packet[0] == 3
            video.append(packet[9:])
        assert video and video[0][0] == 1
    out = io.BytesIO()
    Image.new('RGB', (320, 240), 'green').save(out, format='JPEG')
    jpeg = out.getvalue()
    calls = []
    enabled = False
    index = 0

    async def command(op, **args):
        nonlocal enabled, index
        calls.append((op, args))
        if op == 'video': enabled = args['enabled']; index = 0
        if op == 'keyframe': index = 0
        return {}
    runtime = Runtime(SimpleNamespace(app_id='browser'))
    session = Session('fixture', 'test-window', SimpleNamespace(poll=lambda: None),
        helper=SimpleNamespace(command=command), main=1)
    meta = {'wid': 1, 'windowClass': 'pantheon-page-fixture', 'title': 'Synthetic RTC fixture',
        'geometry': {'x': 0, 'y': 0, 'w': 320, 'h': 240}, 'transientFor': None, 'overrideRedirect': False}
    if video: meta['videoCodec'] = 'h264'
    runtime.sessions[session.id] = session
    runtime.windows[1] = (session, 1, meta)
    session.windows[1] = 1
    session.frames[1] = jpeg
    app = web.Application()
    app.router.add_get('/native-stream', runtime.websocket)
    app.router.add_get('/viewer.mjs', lambda _: web.Response(body=bundle, content_type='text/javascript'))
    app.router.add_get('/', lambda _: web.Response(text='<body></body>', content_type='text/html'))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async def frames():
        nonlocal index
        stamp = 0
        while True:
            runtime.frame(session, 1, jpeg)
            if 2 in runtime.windows: runtime.frame(session, 2, jpeg)
            if video and enabled:
                source = video[index % len(video)]
                index += 1; stamp += 16667
                runtime.video(session, 1, source[:1] + struct.pack('>Q', stamp) + source[9:])
            await asyncio.sleep(1/60)
    task = asyncio.create_task(frames())
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=['--autoplay-policy=no-user-gesture-required'])
            page = await browser.new_page()
            page.on('pageerror', lambda error: print('PAGE ERROR', error))
            await page.add_init_script('window.peers=[];const Original=RTCPeerConnection; window.RTCPeerConnection=class extends Original { constructor(...a) {super(...a); peers.push(this)} }')
            await page.goto(f'http://127.0.0.1:{port}')
            await page.evaluate('''async ({secret}) => {
                const {createNativeCaptureSession} = await import('/viewer.mjs');
                window.viewer = createNativeCaptureSession('rtc-test', location.origin);
                window.release = viewer.retain();
                await viewer.connect({host: location.host, username: '', password: secret});
                const handle = await viewer.claim('pantheon-page-fixture');
                const host = document.createElement('div'); host.style='width:320px;height:240px'; document.body.append(host);
                viewer.adoptWindow(handle,host);
            }''', {'secret': runtime.secret})
            await page.wait_for_function("document.querySelector('[aria-label=\"Media connection\"]').textContent.startsWith('Direct')", timeout=20000)
            if video:
                try:
                    await page.wait_for_function("document.querySelector('video').dataset.playing === 'true' && document.querySelector('video').videoWidth === 320", timeout=15000)
                except Exception:
                    print(await page.evaluate("async () => ({video:document.querySelector('video').outerHTML, tracks:peers.at(-1).getReceivers().map(r=>({id:r.track.id,kind:r.track.kind})), stats:[...await peers.at(-1).getStats()].map(x=>x[1]).filter(x=>['inbound-rtp','codec'].includes(x.type))})"))
                    print('helper calls', calls[-10:], 'enabled', enabled, 'peer', next(iter(runtime.clients.values()))['peer'].process.returncode)
                    raise
            await page.evaluate("viewer.windows()[0].focus(); viewer.sendKey(new KeyboardEvent('keydown', {code:'KeyA', key:'a'}))")
            for _ in range(100):
                if any(op == 'input' and a.get('code') == 'KeyA' for op, a in calls): break
                await asyncio.sleep(.05)
            assert any(op == 'input' and a.get('code') == 'KeyA' for op, a in calls)
            state = await page.locator('[aria-label="Media connection"]').inner_text()
            print('PASS authenticated Pion/Chromium loopback, decoded media and DataChannel input:', state)
            # A new dialog renegotiates media while retaining the main handle.
            await page.evaluate('window.mainHandle = viewer.windows()[0]')
            popup = {**meta, 'wid': 2, 'windowClass': 'pantheon-dialog-2', 'transientFor': 1}
            popup.pop('videoCodec', None)
            session.windows[2] = 2
            runtime.windows[2] = (session, 2, popup)
            runtime.event('open', window=popup)
            await page.wait_for_function("viewer.windows().length === 2 && peers.length >= 2 && peers.at(-1).connectionState === 'connected'")
            await page.wait_for_function("document.querySelector('[aria-label=\"Media connection\"]').textContent.startsWith('Direct')")
            assert await page.evaluate('viewer.windows()[0] === mainHandle')
            runtime.windows.pop(2); session.windows.pop(2); session.frames.pop(2, None)
            runtime.event('close', wid=2)
            await page.wait_for_function("viewer.windows().length === 1 && peers.length >= 3 && peers.at(-1).connectionState === 'connected'")
            await page.wait_for_function("document.querySelector('[aria-label=\"Media connection\"]').textContent.startsWith('Direct')")
            assert await page.evaluate('viewer.windows()[0] === mainHandle')
            print('PASS dialog add/remove preserves main window and renegotiates direct media')
            # Kill only this fixture's media subprocess; the app/gateway stays up.
            client = next(iter(runtime.clients.values()))
            client['peer'].process.kill()
            await page.wait_for_function("document.querySelector('[aria-label=\"Media connection\"]').textContent.startsWith('Gateway')", timeout=5000)
            assert not client['direct']
            await page.wait_for_function("document.querySelector('canvas').width === 320")
            await page.evaluate("viewer.windows()[0].focus(); viewer.sendKey(new KeyboardEvent('keydown', {code:'KeyB', key:'b'}))")
            for _ in range(100):
                if any(op == 'input' and a.get('code') == 'KeyB' for op, a in calls): break
                await asyncio.sleep(.05)
            assert any(op == 'input' and a.get('code') == 'KeyB' for op, a in calls)
            print('PASS failed P2P recovers gateway media/input without restarting the app')
            await browser.close()
            for _ in range(100):
                if not runtime.clients: break
                await asyncio.sleep(.05)
            assert not runtime.clients
            assert any(op == 'input' and a.get('code') == 'KeyB' and a.get('down') is False for op,a in calls)
            print('PASS viewer disconnect releases held input and stops its P2P subprocess')
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError): await task
        await runner.cleanup()

asyncio.run(main())
