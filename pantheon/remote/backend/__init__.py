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

# Import SERVER_URLS for backward compatibility
try:
    from pantheon.toolsets.utils.constant import SERVER_URLS
except ImportError:
    # pantheon.toolset not available, use default
    SERVER_URLS = ["wss://magique1.aristoteleo.com/ws"]

__all__ = [
    "RemoteBackend",
    "RemoteService",
    "RemoteWorker",
    "BackendRegistry",
    "SERVER_URLS",
]
