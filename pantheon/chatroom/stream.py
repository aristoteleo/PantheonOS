"""NATS Stream Adapter - Optional streaming message publishing for ChatRoom"""

import time
from typing import TYPE_CHECKING

from ..utils.log import logger

if TYPE_CHECKING:
    from .room import ChatRoom


class NATSStreamAdapter:
    """Adapter for adding NATS streaming capability to ChatRoom"""

    def __init__(self):
        self._backend = None

    async def _get_backend(self):
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

    def create_hooks(self, chat_id: str):
        """Create NATS hooks for chat() method

        Returns:
            tuple: (chunk_hook, step_hook)
        """

        async def chunk_hook(chunk: dict):
            await self.publish(chat_id, "chunk", {"type": "chunk", "chunk": chunk})

        async def step_hook(step_message: dict):
            # Filter out user messages to avoid duplication on frontend
            if step_message.get("role") == "user":
                return
            await self.publish(
                chat_id,
                "step",
                {"type": "step_message", "step_message": step_message},
            )

        return chunk_hook, step_hook

    async def publish_chat_finished(self, chat_id: str):
        """Publish chat finished message"""
        await self.publish(chat_id, "chat_finished", {"type": "chat_finished"})
