import os
from typing import Dict, Any
from dataclasses import dataclass, field
from .backend import SERVER_URLS


@dataclass
class RemoteConfig:
    """Configuration for remote backend"""

    backend: str = "magique"  # Default to magique for backward compatibility
    backend_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        backend: str | None = None,
        backend_config: Dict[str, Any] | None = None,
        **kwargs,
    ) -> "RemoteConfig":
        """Create config from input parameters with environment fallback"""
        # Use input backend or fallback to env
        if backend is None:
            backend = os.getenv("PANTHEON_REMOTE_BACKEND", "magique")

        # Use input backend_config or create from env
        if backend_config is None:
            if backend == "nats":
                servers_env = os.getenv("NATS_SERVERS", "nats://localhost:4222")
                server_urls = [s.strip() for s in servers_env.split(",")]
                backend_config = {"server_urls": server_urls}
            else:  # magique
                backend_config = {"server_urls": SERVER_URLS}

        # Merge any additional kwargs into backend_config
        if kwargs:
            backend_config = {**backend_config, **kwargs}

        return cls(backend=backend, backend_config=backend_config)

    @classmethod
    def from_env(cls) -> "RemoteConfig":
        """Create config from environment variables (kept for backward compatibility)"""
        return cls.from_config()
