"""Push an in-memory bytes buffer to a NATS inbox in small chunks.

Same wire protocol as the file_transfer ``stream_read`` push path, so the
frontend's binary stream receiver can consume either:

    per message: 4-byte big-endian uint32 seq + 1-byte flags (bit0=EOF,
                 bit1=ERROR) + payload (absent for EOF/ERROR markers)

Keeping every wire message small (~64 KiB) and one-in-flight is the only
pattern that doesn't jam the Modal→DO-LB→nginx→NATS egress (≥128 KiB or
concurrency → ~6 MiB freeze). Windowed flow control (caller acks the highest
contiguous seq) bounds unacked data so a slow consumer can't be dropped.

Used by chatroom.room.stream_chat_messages to ship a long chat's history fast
(~3.5 MB/s) instead of the byte-budgeted request/reply pagination (~0.7 MB/s).
"""
from __future__ import annotations

import asyncio
import struct

from pantheon.utils.log import logger

_CANCEL = 0xFFFFFFFF


async def push_bytes_stream(
    nc,
    data: bytes,
    reply_to: str,
    ack_subject: str,
    chunk_size: int = 64 * 1024,
    window: int = 192,
    ack_timeout: float = 15.0,
) -> None:
    """Stream ``data`` to ``reply_to`` as windowed binary chunks.

    Args:
        nc: connected NATS client (publishes + subscribes).
        data: the bytes to send.
        reply_to: subject the caller is subscribed to (chunks land here).
        ack_subject: subject the caller publishes acks on (big-endian uint32 =
            highest contiguous seq received; 0xFFFFFFFF cancels).
        chunk_size: payload bytes per message (clamped ≤ 64 KiB).
        window: max chunks the pusher gets ahead of the last ack.
        ack_timeout: abort if blocked on the window with no ack for this long.
    """
    chunk_size = max(4096, min(int(chunk_size), 64 * 1024))
    window = max(8, min(int(window), 1024))

    state = {"acked": -1, "cancel": False}
    ev = asyncio.Event()

    async def on_ack(msg):
        try:
            if len(msg.data) >= 4:
                s = struct.unpack(">I", msg.data[:4])[0]
                if s == _CANCEL:
                    state["cancel"] = True
                elif s > state["acked"]:
                    state["acked"] = s
        except Exception:
            pass
        ev.set()

    sub = await nc.subscribe(ack_subject, cb=on_ack)
    total = len(data)
    seq = 0
    pos = 0
    try:
        while pos < total:
            if state["cancel"]:
                return
            while (seq - state["acked"] - 1) >= window and not state["cancel"]:
                ev.clear()
                try:
                    await asyncio.wait_for(ev.wait(), ack_timeout)
                except asyncio.TimeoutError:
                    logger.warning("[push_bytes_stream] ack timeout, aborting")
                    return
            if state["cancel"]:
                return
            chunk = data[pos:pos + chunk_size]
            await nc.publish(reply_to, struct.pack(">IB", seq, 0) + chunk)
            await nc.flush()
            pos += len(chunk)
            seq += 1
        if not state["cancel"]:
            await nc.publish(reply_to, struct.pack(">IB", seq, 1))  # EOF
            await nc.flush()
    except Exception as e:
        logger.error(f"[push_bytes_stream] error: {e}")
        try:
            await nc.publish(reply_to, struct.pack(">IB", seq, 2))  # ERROR
            await nc.flush()
        except Exception:
            pass
    finally:
        try:
            await sub.unsubscribe()
        except Exception:
            pass
