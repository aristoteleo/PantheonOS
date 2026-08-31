"""
IOPub Message Handlers for Jupyter Kernel

Handlers for processing IOPub messages from kernel execution.
Each handler is a callable with signature:
    async def handler(session_id: str, message: JupyterMessage, metadata: dict) -> None
"""

import hashlib
import json
import time
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from pantheon.remote.backend.base import RemoteBackend, StreamMessage, StreamType
from pantheon.utils.log import logger
from pantheon.utils.misc import run_func

if TYPE_CHECKING:
    from .jupyter_kernel import JupyterMessage


# ============================================================================
# Event Bus Classes (for NATS streaming)
# ============================================================================

class IOPubEventBus(ABC):
    """Abstract IOPub event publishing system"""

    @abstractmethod
    async def publish(
        self, session_id: str, message: "JupyterMessage", metadata: Optional[dict] = None
    ) -> None:
        """Publish message to stream"""
        pass


class RemoteIOPubEventBus(IOPubEventBus):
    """
    IOPub event bus implementation based on unified remote backend

    This class is primarily designed for PUBLISHING IOPub messages from backend to frontend.
    For SUBSCRIBING to messages, use frontend StreamChannel.subscribe() directly for better performance.
    """

    def __init__(self, remote_backend: RemoteBackend):
        self.remote_backend = remote_backend
        self.stream_channels: Dict[str, Any] = {}  # stream_id -> StreamChannel

    async def publish(
        self, session_id: str, message: "JupyterMessage", metadata: Optional[dict] = None
    ) -> None:
        """Convert Jupyter message to stream message and publish with optional metadata"""
        try:
            # Import here to avoid circular dependency
            from .jupyter_kernel import make_json_serializable
            
            # Encode session_id to avoid NATS subject special characters (. : * >)
            # NATS treats . as token separator in subjects, so "chat:analysis.ipynb"
            # would be parsed incorrectly. URL encoding ensures proper handling.
            safe_session_id = urllib.parse.quote(session_id, safe="")
            stream_id = f"notebook_iopub_{safe_session_id}"

            # Get or create stream channel
            if stream_id not in self.stream_channels:
                self.stream_channels[
                    stream_id
                ] = await self.remote_backend.get_or_create_stream(
                    stream_id, StreamType.NOTEBOOK
                )

            stream_channel = self.stream_channels[stream_id]

            # Send Jupyter message directly (simplified structure)
            jupyter_message_dict = make_json_serializable(message.to_dict())

            # Prepare stream metadata (merge default + provided metadata)
            stream_metadata = {
                "source": "jupyter_kernel",
            }
            if metadata:
                stream_metadata.update(metadata)

            stream_message = StreamMessage(
                type=StreamType.NOTEBOOK,
                session_id=session_id,
                timestamp=time.time(),
                data=jupyter_message_dict,  # Direct Jupyter message
                metadata=stream_metadata,  # Enhanced metadata support
            )

            # Publish to stream channel
            await stream_channel.publish(stream_message)
            logger.debug(
                f"Published IOPub message to stream {stream_id}: {message.msg_type}"
            )

        except Exception as e:
            logger.error(
                f"Failed to publish IOPub message for session {session_id}: {e}"
            )
            # Don't raise - avoid blocking other handlers and causing error loops

    async def cleanup(self):
        """Clean up all stream channels"""
        try:
            # Close all stream channels
            for stream_id, channel in self.stream_channels.items():
                try:
                    await channel.close()
                    logger.debug(f"Closed stream channel: {stream_id}")
                except Exception as e:
                    logger.warning(f"Error closing stream channel {stream_id}: {e}")

            self.stream_channels.clear()
            logger.info("RemoteIOPubEventBus cleanup completed")

        except Exception as e:
            logger.error(f"Error during IOPub event bus cleanup: {e}")


# ============================================================================
# IOPub Message Handlers
# ============================================================================


class NatsStreamHandler:
    """
    Publishes IOPub messages to NATS stream.
    
    Used for real-time streaming to remote frontends.
    Manages its own RemoteIOPubEventBus lifecycle.
    """
    
    def __init__(self, remote_backend: RemoteBackend):
        self.event_bus = RemoteIOPubEventBus(remote_backend)
    
    async def __call__(
        self, session_id: str, message: "JupyterMessage", metadata: dict
    ) -> None:
        await self.event_bus.publish(session_id, message, metadata)
    
    async def cleanup(self):
        """Cleanup NATS event bus resources"""
        await self.event_bus.cleanup()
        logger.info("NatsStreamHandler: cleaned up event_bus")


class FileLogHandler:
    """
    Records kernel execution to JSONL log files.
    
    Log format (one JSON per line):
        {"ts": 1704123456.789, "type": "execute_result", "content": {...}, "meta": {...}}
    
    Filename: {notebook_name}_{path_hash}_execution.jsonl
    Uses notebook_path from execution metadata for unique identification.
    """
    
    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"FileLogHandler initialized: {self.log_dir}")

    @staticmethod
    def _write_log(log_file: Path, entry: dict) -> None:
        """Write log entry to file (blocking)"""
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    async def __call__(
        self, session_id: str, message: "JupyterMessage", metadata: dict
    ) -> None:
        try:
            # Get notebook name from metadata, fallback to session_id
            notebook_path = metadata.get("notebook_path") if metadata else None
            if notebook_path:
                # Extract notebook name and create path hash for uniqueness
                notebook_name = Path(notebook_path).stem
                path_hash = hashlib.md5(notebook_path.encode()).hexdigest()[:6]
                log_name = f"{notebook_name}_{path_hash}"
            else:
                # Fallback to session_id (first 8 chars)
                log_name = session_id[:8]
            
            log_file = self.log_dir / f"{log_name}_execution.jsonl"
            entry = {
                "ts": time.time(),
                "type": message.msg_type,
                "content": message.content,
            }
            if metadata:
                entry["meta"] = metadata
            
            # Use run_func to execute blocking I/O in thread pool
            await run_func(self._write_log, log_file, entry)
        
        except Exception as e:
            logger.warning(f"FileLogHandler write failed: {e}")
