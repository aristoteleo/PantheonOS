from typing import Optional
from .config import RemoteConfig
from .backend.registry import BackendRegistry
from .backend.base import RemoteBackend, RemoteService


class RemoteBackendFactory:
    """Factory for creating remote backends"""

    @staticmethod
    def create_backend(config: Optional[RemoteConfig] = None) -> RemoteBackend:
        """Create remote backend from configuration"""
        if config is None:
            config = RemoteConfig.from_config()

        backend_class = BackendRegistry.get_backend(config.backend)
        return backend_class(**config.backend_config)

    @staticmethod
    def register_backends():
        """Register all available backends"""
        from .backend.magique import MagiqueBackend
        from .backend.nats import NATSBackend

        BackendRegistry.register("magique", MagiqueBackend)
        BackendRegistry.register("nats", NATSBackend)


# Auto-register backends on import
RemoteBackendFactory.register_backends()


async def connect_remote(
    service_id_or_name: str, server_urls=None, **kwargs
) -> RemoteService:
    # Create config with optional server_urls override (unified as servers)
    config_kwargs = {}
    if server_urls is not None:
        config_kwargs["server_urls"] = server_urls

    config = RemoteConfig.from_config(**config_kwargs)
    backend = RemoteBackendFactory.create_backend(config)
    return await backend.connect(service_id_or_name, **kwargs)
