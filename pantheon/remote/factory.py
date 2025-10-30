from typing import Optional
from .backend.registry import BackendRegistry
from .backend.base import RemoteBackend, RemoteService
import os
from typing import Dict, Any
from dataclasses import dataclass, field


def resolve_backend_config(
    backend: str | None = None, explicit_config: Dict[str, Any] | None = None
):
    """Resolve backend configuration with clear precedence: explicit > env > defaults"""
    if backend is None:
        backend = os.getenv("PANTHEON_REMOTE_BACKEND", "nats")

    # default and env
    servers_env = ""
    if backend == "nats":
        # Default config
        config = {"server_urls": ["nats://localhost:4222"]}
        servers_env = os.getenv("NATS_SERVERS", "")
    else:
        raise ValueError(f"Unknown backend: {backend}")

    if servers_env:
        config["server_urls"] = [s.strip() for s in servers_env.split("|") if s.strip()]

    # Explicit config override (highest priority)
    if explicit_config:
        config.update({k: v for k, v in explicit_config.items() if v})

    return backend, config


@dataclass
class RemoteConfig:
    """Configuration for remote backend"""

    backend: str = "nats"  # Default to NATS
    backend_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls, backend: str | None = None, backend_config: Dict[str, Any] | None = None
    ) -> "RemoteConfig":
        """Create config from input parameters with environment fallback"""
        # Use centralized resolver for backend config
        backend, resolved_config = resolve_backend_config(backend, backend_config)

        return cls(backend=backend, backend_config=resolved_config)

    @classmethod
    def from_env(cls) -> "RemoteConfig":
        """Create config from environment variables (kept for backward compatibility)"""
        return cls.from_config()


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
        from .backend.nats import NATSBackend

        BackendRegistry.register("nats", NATSBackend)


# Auto-register backends on import
RemoteBackendFactory.register_backends()


async def connect_remote(
    service_id_or_name: str, server_urls=None, backend=None, **kwargs
) -> RemoteService:
    config = RemoteConfig.from_config(
        backend=backend, backend_config={"server_urls": server_urls}
    )
    backend = RemoteBackendFactory.create_backend(config)
    return await backend.connect(service_id_or_name, **kwargs)
