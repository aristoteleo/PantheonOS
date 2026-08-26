"""Decode the browser stream ourselves, outside any browser.

    python scripts/check_browser_stream.py <browser-cast url> [frame.png]

A headless Chrome cannot be used to check this: it decodes H.264 fine and
then composites flat grey into its raster, so both canvas readback and CDP
screenshots report a frozen picture for a stream that is demonstrably
moving. This connects to the same browser-cast socket the gateway uses,
decodes the packets with PyAV, and answers the three questions that
actually matter — is there a picture, does it change, and does input reach
the page (the page's own scroll position rides on the status channel).

Get the URL from the desktop: it is the app's frame_url with
"browser-frame" replaced by "browser-cast", plus "&page=<page_id>".
"""
import asyncio, json, sys, time
import numpy as np

URL = sys.argv[1].replace('https://', 'wss://').replace('http://', 'ws://')

async def main():
    import aiohttp, av
    dec = av.CodecContext.create('h264', 'r')
    frames = 0
    nbytes = 0
    t0 = None
    stats = []
    scrolls = []
    keep = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL, max_msg_size=0, heartbeat=30) as ws:
            meta = None
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                msg = await asyncio.wait_for(ws.receive(), timeout=20)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    meta = json.loads(msg.data)
                    st = (meta.get('status') or {}).get('scroll')
                    if st and st not in scrolls:
                        scrolls.append(st)
                    if t0 is None:
                        t0 = time.monotonic()
                        deadline = t0 + 8
                        print('codec', meta.get('codec'), meta.get('w'), 'x', meta.get('h'))
                        if meta.get('codec') != 'h264':
                            dec = av.CodecContext.create('libvpx', 'r')
                        await ws.send_json({'events': [{'t': 'wheel', 'dy': 120, 'x': 400, 'y': 400}]})
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    nbytes += len(msg.data)
                    frames += 1
                    for fr in dec.decode(av.Packet(msg.data)):
                        img = fr.to_ndarray(format='rgb24')
                        stats.append((img.mean(), len(np.unique(img[::16, ::16].reshape(-1, 3), axis=0))))
                        keep.append(img) if len(keep) < 400 else None
                    await ws.send_json({'events': [{'t': 'wheel', 'dy': 120, 'x': 400, 'y': 400}]})
                else:
                    break
    secs = max(0.001, time.monotonic() - (t0 or time.monotonic()))
    print(f'{frames/secs:.1f} fps over the wire, {nbytes*8/secs/1e6:.1f} Mbps')
    if stats:
        means = [round(m, 1) for m, _ in stats]
        colours = [c for _, c in stats]
        print(f'decoded {len(stats)} frames; mean brightness {min(means)}..{max(means)}; '
              f'colours {min(colours)}..{max(colours)}')
        print('picture CHANGES' if len(set(means)) > 2 else 'picture IS STATIC')
        print('BLANK (flat grey)' if max(colours) < 20 else 'REAL CONTENT')
    print('scroll positions seen:', scrolls[:6], '...' if len(scrolls) > 6 else '')
    print('THE PAGE SCROLLED' if len(scrolls) > 1 else 'THE PAGE DID NOT SCROLL')
    if keep and len(sys.argv) > 2:
        from PIL import Image
        # Mid-scroll, not the tail: by the end the probe has let go and the
        # window may already be gone.
        Image.fromarray(keep[len(keep) // 2]).save(sys.argv[2])
        print('saved', sys.argv[2], f'(frame {len(keep)//2} of {len(keep)})')

asyncio.run(main())
