"""Private, PID-scoped connection to Fleet's signed native capture helper."""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
import struct
from pathlib import Path

MAX_PACKET = 16 * 1024 * 1024 + 16


def executable():
    path = Path(os.environ.get('PANTHEON_NATIVE_CAPTURE_HELPER', ''))
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError('Update Fleet on this node: the native capture helper is missing')
    return str(path)


async def probe():
    process = await asyncio.create_subprocess_exec(executable(), '--probe', stdout=asyncio.subprocess.PIPE)
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), 10)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    result = json.loads(raw)
    if result.get('protocol') != 1 or not result.get('available'):
        raise RuntimeError('Native capture requires an unlocked, interactive desktop on this node')
    if not result.get('interactive', True):
        raise RuntimeError('Unlock the desktop and wake its display on this Fleet node before streaming')
    if not result.get('screen_recording') or not result.get('input'):
        raise RuntimeError('Allow Fleet Screen Recording and Accessibility in macOS System Settings, then restart Fleet. Run fleet capture permissions on the node to open permission requests.')
    return result


class Helper:
    def __init__(self, process, on_frame, on_error, on_video=None):
        self.process, self.on_frame, self.on_error = process, on_frame, on_error
        self.on_video = on_video
        self.pending = {}
        self.sequence = 0
        self.write_lock = asyncio.Lock()
        self.reader = asyncio.create_task(self.read())

    @classmethod
    async def start(cls, pid, on_frame, on_error, on_video=None):
        process = await asyncio.create_subprocess_exec(executable(), '--pid', str(pid),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        return cls(process, on_frame, on_error, on_video)

    async def read(self):
        try:
            while True:
                size, = struct.unpack('>I', await self.process.stdout.readexactly(4))
                if not 1 <= size <= MAX_PACKET:
                    raise ValueError('Invalid native capture packet length')
                packet = await self.process.stdout.readexactly(size)
                if packet[0] == 2:
                    if size < 12 or packet[9:11] != b'\xff\xd8':
                        raise ValueError('Invalid native capture frame')
                    self.on_frame(struct.unpack('>Q', packet[1:9])[0], packet[9:])
                elif packet[0] == 3:
                    if size < 22 or packet[18:22] != b'\x00\x00\x00\x01':
                        raise ValueError('Invalid native video frame')
                    if self.on_video:
                        self.on_video(struct.unpack('>Q', packet[1:9])[0], packet[9:])
                elif packet[0] == 1:
                    value = json.loads(packet[1:])
                    future = self.pending.get(value.get('id'))
                    if future and not future.done():
                        if value.get('ok'):
                            future.set_result(value)
                        else:
                            future.set_exception(RuntimeError(value.get('error', 'Native capture failed')))
                    elif value.get('event'):
                        message = value.get('error', 'Native capture stopped')
                        self.on_error(message)
                        if value['event'] == 'fatal':
                            for pending in self.pending.values():
                                if not pending.done():
                                    pending.set_exception(RuntimeError(message))
                            return
                else:
                    raise ValueError('Unknown native capture packet')
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self.on_error(str(error) if not isinstance(error, asyncio.IncompleteReadError) else 'Native capture helper exited')
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Native capture connection closed'))

    async def command(self, op, **args):
        if self.reader.done():
            raise RuntimeError('Native capture helper is no longer connected')
        self.sequence += 1
        key = str(self.sequence)
        raw = json.dumps({**args, 'op': op, 'id': key}, allow_nan=False).encode() + b'\n'
        if len(raw) > 65536:
            raise ValueError('Native command exceeds 64 KB')
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            async with self.write_lock:
                self.process.stdin.write(raw)
                await self.process.stdin.drain()
            return await asyncio.wait_for(future, 15)
        finally:
            self.pending.pop(key, None)

    async def close(self):
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.reader
