"""NATS Stream Adapter - Optional streaming message publishing for ChatRoom"""

import time

from pantheon.utils.log import logger

from .event_hooks import ChatEventHooks


class NATSStreamAdapter(ChatEventHooks):
    """Adapter for adding NATS streaming capability to ChatRoom"""

    def __init__(self):
        self._backend = None
        self._closed = False

    async def close(self):
        """Dispose only this adapter's transport after Agent work has drained."""
        self._closed = True
        backend = self._backend
        if backend is not None:
            await backend.close()
            self._backend = None

    async def _get_backend(self):
        if self._closed:
            raise RuntimeError("Agent event stream is closed")
        if self._backend is None:
            from pantheon.remote import RemoteBackendFactory

            self._backend = RemoteBackendFactory.create_backend()
        return self._backend

    async def publish(self, chat_id: str, message_type: str, data: dict):
        """Publish message to NATS Stream"""
        from pantheon.remote.backend.base import StreamMessage, StreamType

        backend = await self._get_backend()
        message = StreamMessage(
            type=StreamType.CHAT,
            session_id=f"chat_{chat_id}",
            timestamp=time.time(),
            data={**data, "chat_id": chat_id},
        )
        channel = await backend.get_or_create_stream(f"chat_{chat_id}", StreamType.CHAT)
        try:
            await channel.publish(message)
        except Exception as e:
            logger.error(f"Error publishing stream: {e}")

    async def publish_stream(self, stream_id: str, data: dict) -> bool:
        """Publish to a named stream that is NOT a chat.

        The desktop is the pod's, not a conversation's: every viewport of this
        pod listens on one stream regardless of which chat it has open, which
        is what lets a window opened from one conversation show up in a
        standalone desktop page that has none. Both ends derive the subject as
        `<prefix>.pantheon.stream.<id>`, and the prefix is this pod's.

        Return whether the transport accepted the publish. Broadcast callers
        may ignore this best-effort result; directed request callers must not
        wait for an answer to a publish that already failed locally.
        """
        from pantheon.remote.backend.base import StreamMessage, StreamType

        backend = await self._get_backend()
        message = StreamMessage(
            type=StreamType.CUSTOM,
            session_id=stream_id,
            timestamp=time.time(),
            data=data,
        )
        channel = await backend.get_or_create_stream(stream_id, StreamType.CUSTOM)
        try:
            await channel.publish(message)
            return True
        except Exception as e:
            logger.error(f"Error publishing stream {stream_id}: {e}")
            return False
