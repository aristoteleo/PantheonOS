"""ToolsetProxy - Unified proxy class for accessing remote toolsets.

This module provides a proxy layer between Agents and ToolSets, enabling:
- Lazy loading of function descriptions
- Permanent caching (no TTL expiration)
- Instance pooling (same endpoint+toolset returns same instance)
- Unified access to endpoint-routed or direct toolset connections
"""

import asyncio
from typing import Dict, List, Optional, Any, Union
from enum import Enum

from ..utils.log import logger
from ..remote import RemoteService


class ProxyMode(Enum):
    """Proxy connection mode."""

    ENDPOINT_INSTANCE = "endpoint_instance"  # Route through endpoint instance
    ENDPOINT_ID = "endpoint_id"  # Route through endpoint by service_id
    TOOLSET_ID = "toolset_id"  # Direct connection to toolset by service_id


class ToolsetProxy:
    """Unified proxy class for accessing remote toolsets.

    Implements instance pooling - same endpoint+toolset returns same instance.

    Factory methods (support both service_id string and service object):
    - from_endpoint(endpoint, toolset_name) - Route through endpoint (recommended)
    - from_toolset(service_or_id) - Direct toolset connection (legacy)
    """

    # Class-level instance pool: {pool_key: instance}
    _instance_pool: Dict[str, "ToolsetProxy"] = {}
    _pool_lock = asyncio.Lock()

    def __new__(
        cls,
        mode: ProxyMode,
        toolset_name: str,
        **kwargs,
    ):
        """Control instance creation - return existing instance if available.

        Pooling key: mode + toolset_name + service_id (or endpoint id)
        """
        # Generate pool key based on mode and identifiers
        if mode == ProxyMode.ENDPOINT_INSTANCE:
            endpoint = kwargs.get("endpoint")
            # Use object id for endpoint instances (not poolable across different instances)
            pool_key = f"{mode.value}:{toolset_name}:{id(endpoint)}"
        elif mode == ProxyMode.ENDPOINT_ID:
            service_id = kwargs.get("service_id")
            pool_key = f"{mode.value}:{toolset_name}:{service_id}"
        else:  # TOOLSET_ID
            service_id = kwargs.get("service_id")
            pool_key = f"{mode.value}:{toolset_name}:{service_id}"

        # Check pool for existing instance
        if pool_key in cls._instance_pool:
            instance = cls._instance_pool[pool_key]
            logger.debug(
                f"ToolsetProxy: Reusing pooled instance for {toolset_name} (key={pool_key})"
            )
            return instance

        # Create new instance
        instance = super().__new__(cls)
        cls._instance_pool[pool_key] = instance

        # Store pool key for debugging
        instance._pool_key = pool_key
        instance._is_initialized = False  # Flag to prevent re-init

        logger.debug(
            f"ToolsetProxy: Created new pooled instance for {toolset_name} (key={pool_key})"
        )

        return instance

    def __init__(
        self,
        mode: ProxyMode,
        toolset_name: str,
        **kwargs,
    ):
        """Initialize ToolsetProxy (use factory methods instead).

        Note: Due to instance pooling, __init__ may be called multiple times
        on the same instance. We guard against re-initialization.
        """
        # Guard against re-initialization (instance pooling)
        if self._is_initialized:
            return

        self.mode = mode
        self.toolset_name = toolset_name

        # Mode-specific attributes with type annotations
        self.endpoint: Optional[Any] = (
            None  # Endpoint instance (ENDPOINT_INSTANCE mode)
        )
        self.service: Optional[Any] = (
            None  # Remote service (ENDPOINT_ID/TOOLSET_ID modes)
        )
        self.service_id: Optional[str] = None  # Service ID for lazy connection

        if mode == ProxyMode.ENDPOINT_INSTANCE:
            self.endpoint = kwargs.get("endpoint")
        elif mode == ProxyMode.ENDPOINT_ID:
            self.service_id = kwargs.get("service_id")
        else:  # TOOLSET_ID
            self.service_id = kwargs.get("service_id")

        # Cache (simple permanent cache, no TTL)
        self._tools_cache: Optional[List[Dict]] = None
        self._lock = asyncio.Lock()

        # Mark as initialized
        self._is_initialized = True

        logger.debug(
            f"ToolsetProxy initialized: mode={self.mode.value}, "
            f"toolset={toolset_name}, "
            f"service_id={kwargs.get('service_id', 'N/A')}, "
            f"pool_key={self._pool_key}"
        )

    @classmethod
    def from_endpoint(
        cls,
        endpoint: Union[str, RemoteService, Any],
        toolset_name: str,
    ):
        """Create proxy routing through endpoint (recommended).

        Args:
            endpoint: str (service_id), RemoteService instance, or Endpoint instance
        """
        # Case 1: service_id string provided - lazy connection
        if isinstance(endpoint, str):
            return cls(
                mode=ProxyMode.ENDPOINT_ID,
                toolset_name=toolset_name,
                service_id=endpoint,
            )

        # Case 2: RemoteService instance (e.g., NATSService)
        # Use service directly, avoid reconnecting
        elif isinstance(endpoint, RemoteService):
            proxy = cls(
                mode=ProxyMode.ENDPOINT_ID,
                toolset_name=toolset_name,
                service_id=endpoint.service_id,
            )
            # Directly assign service to avoid reconnecting
            proxy.service = endpoint
            return proxy

        # Case 3: Endpoint instance (has proxy_toolset method) - use directly
        else:
            return cls(
                mode=ProxyMode.ENDPOINT_INSTANCE,
                toolset_name=toolset_name,
                endpoint=endpoint,
            )

    @classmethod
    def from_toolset(
        cls,
        service_or_id: Union[str, RemoteService],
    ):
        """Create proxy with direct toolset connection (bypass endpoint, legacy).

        Args:
            service_or_id: str (service_id) or RemoteService instance
        """
        # Case 1: service_id string provided - lazy connection
        if isinstance(service_or_id, str):
            return cls(
                mode=ProxyMode.TOOLSET_ID,
                toolset_name=service_or_id,
                service_id=service_or_id,
            )

        # Case 2: RemoteService instance
        # Use service directly, avoid reconnecting
        elif isinstance(service_or_id, RemoteService):
            proxy = cls(
                mode=ProxyMode.TOOLSET_ID,
                toolset_name=service_or_id.service_id,
                service_id=service_or_id.service_id,
            )
            # Directly assign service to avoid reconnecting
            proxy.service = service_or_id
            return proxy

        # Case 3: Unknown type - fallback
        else:
            raise TypeError(
                f"service_or_id must be str or RemoteService, got {type(service_or_id)}"
            )

    async def _ensure_connected(self):
        """Ensure connection established (lazy connect for ENDPOINT_ID/TOOLSET_ID modes)."""
        if self.mode == ProxyMode.ENDPOINT_INSTANCE:
            # Endpoint instance already provided, nothing to do
            # If endpoint is None, let subsequent method calls fail naturally
            return

        # For ENDPOINT_ID and TOOLSET_ID: connect by service_id if needed
        if self.service is None:
            if not self.service_id:
                raise ValueError(f"service_id is None for mode {self.mode.value}")

            from ..remote import connect_remote

            self.service = await connect_remote(self.service_id)
            service_type = (
                "endpoint" if self.mode == ProxyMode.ENDPOINT_ID else "toolset"
            )
            logger.debug(f"Connected to {service_type} service: {self.service_id}")

    async def list_tools(self, force_refresh: bool = False) -> dict:
        """List available tools (permanently cached until force_refresh).

        Returns:
            dict: {"success": True, "tools": [...]}}
                  Complete response from ToolSet.list_tools()
        """
        await self._ensure_connected()

        async with self._lock:
            # Return cached tools if available (unless force refresh)
            if not force_refresh and self._tools_cache is not None:
                logger.debug(f"Using cached tools for {self.toolset_name}")
                return self._tools_cache

            # Fetch fresh tools
            logger.debug(
                f"Fetching tools for {self.toolset_name} (mode: {self.mode.value})"
            )

            result = await self._call_toolset_method("list_tools", {})

            if result.get("success"):
                # Cache and return the complete result dict
                self._tools_cache = result
                tools_count = len(result.get("tools", []))
                logger.debug(f"Cached {tools_count} tools for {self.toolset_name}")
                return result
            else:
                error = result.get("error", "Unknown error")
                raise Exception(f"Failed to list tools: {error}")

    def invalidate_cache(self):
        """Invalidate cache (force refresh on next list_tools)."""
        if self._tools_cache:
            logger.debug(f"Invalidating cache for {self.toolset_name}")
        self._tools_cache = None

    async def _call_toolset_method(self, method_name: str, args: Dict) -> Dict:
        """Call toolset method (mode-dependent routing)."""
        if self.mode == ProxyMode.ENDPOINT_INSTANCE:
            # Direct method call on endpoint instance
            if not self.endpoint:
                raise ValueError("endpoint is None for ENDPOINT_INSTANCE mode")
            return await self.endpoint.proxy_toolset(
                method_name=method_name, args=args, toolset_name=self.toolset_name
            )
        elif self.mode == ProxyMode.ENDPOINT_ID:
            # Remote invoke through endpoint service
            if not self.service:
                raise ValueError("service is None for ENDPOINT_ID mode")
            return await self.service.invoke(
                "proxy_toolset",
                {
                    "method_name": method_name,
                    "args": args,
                    "toolset_name": self.toolset_name,
                },
            )
        else:  # TOOLSET_ID
            # Direct invoke to toolset (bypass endpoint)
            if not self.service:
                raise ValueError("service is None for TOOLSET_ID mode")
            return await self.service.invoke(method_name, args)

    async def invoke(self, method_name: str, args: Optional[Dict] = None) -> Dict:
        """Invoke toolset method (simple passthrough, returns result or raises)."""
        args = args or {}
        await self._ensure_connected()
        logger.debug(
            f"Invoking {self.toolset_name}.{method_name} (mode: {self.mode.value})"
        )
        return await self._call_toolset_method(method_name, args)

    def __repr__(self) -> str:
        """String representation."""
        cache_info = "cached" if self._tools_cache else "no cache"
        return f"ToolsetProxy(toolset={self.toolset_name}, {cache_info})"
