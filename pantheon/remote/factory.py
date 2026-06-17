from typing import Optional
from .backend.registry import BackendRegistry
from pantheon.utils.log import logger
from .backend.base import RemoteBackend, RemoteService
import os
from typing import Dict, Any
from dataclasses import dataclass, field


def resolve_backend_config(
    backend: str | None = None, explicit_config: Dict[str, Any] | None = None
):
    """Resolve backend configuration with clear precedence: explicit > env > settings > defaults"""
    # Try to get config from Settings (lazy import to avoid circular imports)
    # Use mode='safe' to respect dynamically set environment variables (e.g., from --auto-start-nats)
    # This ensures .env file acts as a default fallback, not an override
    try:
        from pantheon.settings import get_settings
        settings = get_settings(mode='safe')
        remote_config = settings.get_remote_config()
    except Exception:
        remote_config = {}
    
    # Backend: explicit > env > settings > default
    if backend is None:
        backend = os.getenv("PANTHEON_REMOTE_BACKEND") or remote_config.get("backend", "nats")

    if backend == "nats":
        # Default config from settings
        config = {"server_urls": remote_config.get("server_urls", ["nats://localhost:4222"])}
        
        # Environment variable override
        servers_env = os.getenv("NATS_SERVERS", "")
        if servers_env:
            config["server_urls"] = [s.strip() for s in servers_env.split("|") if s.strip()]

        # NATS JWT and Seed Support (Dual Auth)
        nats_jwt = remote_config.get("jwt") or os.getenv("NATS_JWT")
        nats_seed = remote_config.get("seed") or os.getenv("NATS_SEED")
        nats_token = remote_config.get("token") or os.getenv("NATS_TOKEN")

        if nats_token:
             config["user"] = "agent"
             config["password"] = nats_token
        elif nats_jwt and nats_seed:
            try:
                # Use nats-py's expected parameter names for JWT/NKey authentication
                import nkeys
                
                # Use manual callbacks to ensure correct NKeys behavior (bypassing file parsing issues)
                import nkeys
                import base64
                
                # Check seed encoding (remove whitespace)
                seed_str = nats_seed.strip()
                jwt_str = nats_jwt.strip()
                
                seed_key = nkeys.from_seed(seed_str.encode())
                public_key = seed_key.public_key.decode()
                logger.debug(f"Manually loaded NKey from seed. Public Key: {public_key}")
                logger.debug(f"JWT (first 20 chars): {jwt_str[:20]}...")

                # Signature callback (must be async coroutine)
                def signature_cb(nonce):
                    logger.debug(f"signature_cb called with nonce={nonce}")
                    if isinstance(nonce, str):
                        nonce = nonce.encode()
                    raw_sig = seed_key.sign(nonce)
                    b64_sig = base64.b64encode(raw_sig)
                    # print(f"DEBUG: Signed nonce {nonce} -> {b64_sig}") # Noisy
                    return b64_sig
                
                # JWT callback (must be async coroutine)
                def user_jwt_cb():
                    logger.debug(f"user_jwt_cb called. Returning JWT first 10 chars: {jwt_str[:10]}")
                    return jwt_str.encode()
                
                config["user_jwt_cb"] = user_jwt_cb
                config["signature_cb"] = signature_cb
                
                # Ensure no conflicting args
                config.pop("nkey", None)
                config.pop("user_credentials", None)
                config.pop("nkeys_seed_str", None)
                
                logger.debug(f"Configured manual JWT/NKey auth callbacks")
            except Exception as e:
                 raise ValueError(f"Failed to setup NATS credentials: {e}")
        elif nats_jwt:
             # Legacy/Simple JWT (unlikely to work without seed in 2.10 secure mode)
             def user_jwt_cb():
                 return nats_jwt.encode() if isinstance(nats_jwt, str) else nats_jwt
             config["user_jwt_cb"] = user_jwt_cb
            
        # Subject prefix
        subject_prefix = remote_config.get("subject_prefix") or os.getenv("NATS_SUBJECT_PREFIX")
        if subject_prefix:
            config["subject_prefix"] = subject_prefix

        # Connection keepalive parameters (optional, for long-running pods)
        ping_interval = os.getenv("NATS_PING_INTERVAL")
        if ping_interval:
            try:
                config["ping_interval"] = int(ping_interval)
                logger.info(f"Configured NATS ping_interval: {ping_interval}s")
            except ValueError:
                logger.warning(f"Invalid NATS_PING_INTERVAL value: {ping_interval}, using default")

        max_reconnect = os.getenv("NATS_MAX_RECONNECT_ATTEMPTS")
        if max_reconnect:
            try:
                config["max_reconnect_attempts"] = int(max_reconnect)
                logger.info(f"Configured NATS max_reconnect_attempts: {max_reconnect}")
            except ValueError:
                logger.warning(f"Invalid NATS_MAX_RECONNECT_ATTEMPTS value: {max_reconnect}, using default")

        reconnect_wait = os.getenv("NATS_RECONNECT_WAIT")
        if reconnect_wait:
            try:
                config["reconnect_time_wait"] = int(reconnect_wait)
                logger.info(f"Configured NATS reconnect_time_wait: {reconnect_wait}s")
            except ValueError:
                logger.warning(f"Invalid NATS_RECONNECT_WAIT value: {reconnect_wait}, using default")

        max_outstanding_pings = os.getenv("NATS_MAX_OUTSTANDING_PINGS")
        if max_outstanding_pings:
            try:
                config["max_outstanding_pings"] = int(max_outstanding_pings)
                logger.info(f"Configured NATS max_outstanding_pings: {max_outstanding_pings}")
            except ValueError:
                logger.warning(f"Invalid NATS_MAX_OUTSTANDING_PINGS value: {max_outstanding_pings}, using default")

        connect_timeout = os.getenv("NATS_CONNECT_TIMEOUT")
        if connect_timeout:
            try:
                config["connect_timeout"] = int(connect_timeout)
                logger.info(f"Configured NATS connect_timeout: {connect_timeout}s")
            except ValueError:
                logger.warning(f"Invalid NATS_CONNECT_TIMEOUT value: {connect_timeout}, using default")

    elif backend == "tcp":
        # Local TCP transport for ChatRoom<->Endpoint (no NATS broker).
        config = {}
        registry_dir = remote_config.get("registry_dir") or os.getenv("PANTHEON_TCP_REGISTRY")
        if registry_dir:
            config["registry_dir"] = registry_dir

    else:
        raise ValueError(f"Unknown backend: {backend}")

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

        try:
            from .backend.tcp import TCPBackend

            BackendRegistry.register("tcp", TCPBackend)
        except ImportError:
            pass


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
