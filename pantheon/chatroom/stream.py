"""Legacy Agent event adapter, retaining its existing hooks and wire format."""

import time

from pantheon.utils.log import logger
from pantheon.remote.streams import NamedStreamPublisher
from .event_hooks import ChatEventHooks


class NATSStreamAdapter(NamedStreamPublisher, ChatEventHooks):
    """Agent-specific events over the shared named-stream transport."""

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
