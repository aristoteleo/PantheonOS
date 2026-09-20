"""Optional Pion transport bundled in Fleet. No pip/media-server dependency."""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
import struct
from pathlib import Path


def configuration():
    binary = Path(os.environ.get('PANTHEON_FLEET_EXECUTABLE', ''))
    if not binary.is_absolute() or not binary.is_file():
        return None
    try:
        servers = json.loads(os.environ.get('PANTHEON_STREAM_ICE_SERVERS', '[]'))
        if not isinstance(servers, list) or len(servers) > 8:
            return None
        for server in servers:
            urls = server.get('urls', [])
            urls = [urls] if isinstance(urls, str) else urls
            if not urls or len(urls) > 8 or any(not isinstance(u, str) or not u.startswith(('stun:', 'stuns:', 'turn:', 'turns:')) for u in urls):
                return None
        return {'binary': str(binary), 'iceServers': servers}
    except (ValueError, TypeError, AttributeError):
        return None


class Peer:
    def __init__(self, process, event):
        self.process, self.event = process, event
        self.commands = asyncio.Queue(maxsize=64)
        self.frames = {}
        self.wait_key = set()
        self.wake = asyncio.Event()
        self.closed = False
        self.writer = asyncio.create_task(self.write())
        self.reader = asyncio.create_task(self.read())

    @classmethod
    async def start(cls, config, offer, event):
        process = await asyncio.create_subprocess_exec(config['binary'], 'capture', 'peer',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=128*1024)
        peer = cls(process, event)
        peer.command({'op': 'offer', 'sdp': offer['sdp'], 'windows': offer['windows'], 'iceServers': config['iceServers']})
        return peer

    def command(self, value):
        raw = json.dumps(value, allow_nan=False).encode()
        if len(raw) > 65535:
            raise ValueError('Peer command exceeds limit')
        self.commands.put_nowait(b'\x01' + raw)
        self.wake.set()

    def frame(self, wid, data, video=False):
        if self.closed:
            return
        key = (wid, video)
        if video:
            # H264 delta frames depend on their predecessors. Dropping one
            # requires a fresh keyframe rather than showing corrupted video.
            if key in self.frames:
                self.wait_key.add(wid)
            if wid in self.wait_key:
                if not data[0]:
                    self.frames.pop(key, None)
                    return
                self.wait_key.discard(wid)
        self.frames[key] = bytes([3 if video else 2]) + struct.pack('>I', wid) + data
        self.wake.set()

    async def write(self):
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                packets = []
                while not self.commands.empty():
                    packets.append(self.commands.get_nowait())
                for wid in tuple(self.wait_key):
                    await self.event({'event': 'keyframe', 'wid': wid})
                packets.extend(self.frames.values())
                self.frames.clear()
                for packet in packets:
                    self.process.stdin.write(struct.pack('>I', len(packet)) + packet)
                    await asyncio.wait_for(self.process.stdin.drain(), 2)
        except asyncio.CancelledError:
            pass
        except Exception:
            await self.event({'event': 'failed'})

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('Invalid peer event')
                await self.event(value)
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        if not self.closed:
            await self.event({'event': 'failed'})

    async def close(self):
        self.closed = True
        self.writer.cancel()
        self.reader.cancel()
        for task in (self.writer, self.reader):
            with contextlib.suppress(asyncio.CancelledError): await task
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 2)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
