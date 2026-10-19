from .backend import RemoteBackend, RemoteService, RemoteWorker
from .backend.registry import BackendRegistry
from .backend.base import StreamType, StreamMessage, StreamChannel
from .factory import RemoteBackendFactory, RemoteConfig
from .remote import connect_remote
from .backend.nats import NATSBackend

# Unified remote backend interface
__all__ = [
    "connect_remote",
    "RemoteConfig",
    "RemoteBackendFactory",
    "RemoteBackend",
    "RemoteService",
    "RemoteWorker",
    "BackendRegistry",
    # Unified backend implementation
    "NATSBackend",
    "StreamType",
    "StreamMessage",
    "StreamChannel",
]
