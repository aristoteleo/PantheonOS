import typing as T
from abc import ABC, abstractmethod
from typing import Any, Dict, Callable, Optional, AsyncGenerator, Union
from dataclasses import dataclass
from funcdesc import Description
from enum import Enum


class StreamType(Enum):
    """流式传输类型"""
    CHAT = "chat"
    NOTEBOOK = "notebook"
    CUSTOM = "custom"


class StreamReliability(Enum):
    """流可靠性级别"""
    FAST = "fast"        # Core NATS - 极速传输，可容忍丢失
    RELIABLE = "reliable"  # JetStream - 可靠传输，消息持久化


@dataclass
class StreamMessage:
    """标准化流式消息格式"""
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
        """从字典创建StreamMessage实例"""
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

    # RPC调用接口
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

    # 流式传输接口
    @abstractmethod
    async def get_or_create_stream(
        self,
        stream_id: str,
        stream_type: StreamType = StreamType.CUSTOM,
        **kwargs
    ) -> "StreamChannel":
        """获取现有流或创建新的流式传输通道"""
        pass



class RemoteService(ABC):
    """Abstract remote service interface"""

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
    """抽象流式传输通道"""

    @abstractmethod
    async def publish(self, message: StreamMessage) -> None:
        """发布消息到流"""
        pass

    @abstractmethod
    async def subscribe(self, callback: Callable[[StreamMessage], None]) -> str:
        """订阅流消息，返回订阅ID"""
        pass

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> bool:
        """取消订阅"""
        pass

    @abstractmethod
    async def close(self) -> None:
        """关闭流通道"""
        pass

    @property
    @abstractmethod
    def stream_id(self) -> str:
        """获取流ID"""
        pass

    @property
    @abstractmethod
    def stream_type(self) -> StreamType:
        """获取流类型"""
        pass


