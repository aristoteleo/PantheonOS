from .backend import RemoteBackend, RemoteService, RemoteWorker
from .backend.registry import BackendRegistry
from .backend.base import StreamType, StreamMessage, StreamChannel
from .factory import RemoteBackendFactory, RemoteConfig
from .remote import connect_remote
from .backend.nats import NATSBackend

# 统一远程后端接口
__all__ = [
    "connect_remote",
    "RemoteConfig",
    "RemoteBackendFactory",
    "RemoteBackend",
    "RemoteService",
    "RemoteWorker",
    "BackendRegistry",
    # 统一后的后端实现
    "NATSBackend",
    "StreamType",
    "StreamMessage",
    "StreamChannel",
]
