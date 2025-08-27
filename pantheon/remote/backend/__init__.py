from .base import RemoteBackend, RemoteService, RemoteWorker
from .registry import BackendRegistry

# Import and register available backends
try:
    from .magique import MagiqueBackend

    BackendRegistry.register("magique", MagiqueBackend)
except ImportError:
    pass

try:
    from .nats import NATSBackend

    BackendRegistry.register("nats", NATSBackend)
except ImportError:
    pass

try:
    from .hypha import HyphaBackend

    BackendRegistry.register("hypha", HyphaBackend)
except ImportError:
    pass

__all__ = [
    "RemoteBackend",
    "RemoteService",
    "RemoteWorker",
    "BackendRegistry",
]
