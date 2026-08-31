import typing as T
from abc import ABC, abstractmethod
from typing import Any, Dict, Callable, Optional, AsyncGenerator, Union
from dataclasses import dataclass
from pantheon.funcdesc import Description
from enum import Enum


class StreamType(Enum):
    """Stream transmission type."""
    CHAT = "chat"
    NOTEBOOK = "notebook"
    CUSTOM = "custom"


class StreamReliability(Enum):
    """Stream reliability level."""
    FAST = "fast"        # Core NATS - Ultra-fast transmission, tolerates loss
    RELIABLE = "reliable"  # JetStream - Reliable transmission, message persistence


@dataclass
class StreamMessage:
    """Standardized stream message format."""
    type: StreamType
    session_id: str
    timestamp: float
    data: Any
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "metadata": self.metadata or {}
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StreamMessage":
        """Create StreamMessage instance from dictionary."""
        return cls(
            type=StreamType(data["type"]),
            session_id=data["session_id"],
            timestamp=data["timestamp"],
            data=data["data"],
            metadata=data.get("metadata")
        )


@dataclass
class ServiceInfo:
    service_id: str
    service_name: str
    description: str
    functions_description: T.Dict[str, Description]


class RemoteBackend(ABC):
    """Abstract interface for remote communication backends with RPC and streaming capabilities"""

    # RPC call interface
    @abstractmethod
    async def connect(self, service_id: str, **kwargs) -> "RemoteService":
        """Connect to a remote service"""
        pass

    @abstractmethod
    def create_worker(self, service_name: str, **kwargs) -> "RemoteWorker":
        """Create a worker for serving functions (synchronous, connection delayed until run)"""
        pass

    @property
    @abstractmethod
    def servers(self) -> list[str]:
        pass

    # Stream transmission interface
    @abstractmethod
    async def get_or_create_stream(
        self,
        stream_id: str,
        stream_type: StreamType = StreamType.CUSTOM,
        **kwargs
    ) -> "StreamChannel":
        """Get existing stream or create new stream transmission channel."""
        pass



class RemoteService(ABC):
    """Abstract remote service interface

    Attributes:
        service_id (str): Service identifier, should be set by subclasses in __init__
    """

    service_id: str  # Subclasses should set this in __init__

    @abstractmethod
    async def invoke(self, method: str, parameters: Dict[str, Any] = None) -> Any:
        """Invoke a remote method"""
        pass

    @abstractmethod
    async def close(self):
        """Close connection"""
        pass

    @property
    @abstractmethod
    def service_info(self) -> ServiceInfo:
        """Get service information"""
        pass

    @abstractmethod
    async def fetch_service_info(self) -> ServiceInfo:
        """"""
        pass


class RemoteWorker(ABC):
    """Abstract remote worker interface"""

    @abstractmethod
    def register(self, func: Callable, **kwargs):
        """Register a function for remote access"""
        pass

    @abstractmethod
    async def run(self):
        """Start the worker"""
        pass

    @abstractmethod
    async def stop(self):
        """Stop the worker"""
        pass

    @property
    @abstractmethod
    def service_id(self) -> str:
        """Get the service ID"""
        pass

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Get the service name"""
        pass

    @property
    def servers(self):
        """Get the servers (optional, for backward compatibility)"""
        return []

    @property
    def functions(self) -> Dict[str, tuple]:
        return {}


class StreamChannel(ABC):
    """Abstract stream transmission channel."""

    @abstractmethod
    async def publish(self, message: StreamMessage) -> None:
        """Publish message to stream."""
        pass

    @abstractmethod
    async def subscribe(self, callback: Callable[[StreamMessage], None]) -> str:
        """Subscribe to stream messages, return subscription ID."""
        pass

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close stream channel."""
        pass

    @property
    @abstractmethod
    def stream_id(self) -> str:
        """Get stream ID."""
        pass

    @property
    @abstractmethod
    def stream_type(self) -> StreamType:
        """Get stream type."""
        pass


