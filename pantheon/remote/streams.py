"""Owned named event transport for Apps and platform services, without Agent code."""
import time

from pantheon.utils.log import logger


class NamedStreamPublisher:
    """Publish custom events using the owner’s configured service bus."""

    def __init__(self):
        self._backend = None
        self._closed = False

    async def close(self):
        """Dispose only this adapter's transport after its owner has drained."""
        self._closed = True
        backend = self._backend
        if backend is not None:
            await backend.close()
            self._backend = None

    async def _get_backend(self):
        if self._closed:
            raise RuntimeError("Event stream is closed")
        if self._backend is None:
            from pantheon.remote import RemoteBackendFactory

            self._backend = RemoteBackendFactory.create_backend()
        return self._backend

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
