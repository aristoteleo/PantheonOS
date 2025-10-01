"""ToolsetProxy - Unified proxy class for accessing remote toolsets.

This module provides a proxy layer between Agents and ToolSets, enabling:
- Lazy loading of function descriptions
- Automatic caching with TTL
- Transparent fault recovery
- Zero-dependency agent creation
- Unified access to endpoint-routed or direct toolset connections
"""

import time
import asyncio
from typing import Dict, List, Optional, Callable, Union
from dataclasses import dataclass
from enum import Enum

from ..utils.log import logger


class ProxyMode(Enum):
    """Proxy connection mode."""
    ENDPOINT_ROUTED = "endpoint_routed"  # Route through endpoint (by toolset_name)
    DIRECT_CONNECTION = "direct_connection"  # Direct connection to toolset service_id


@dataclass
class CachedFunctionDescriptions:
    """Cached function descriptions with timestamp."""
    descriptions: List[Dict]
    timestamp: float
    version: Optional[str] = None


class ToolsetProxy:
    """Unified proxy class for accessing remote toolsets.

    Use factory methods to create proxy instances:
    - from_endpoint(endpoint, toolset_name) - Route through endpoint (recommended)
      endpoint can be an Endpoint instance or endpoint_id string
    - from_toolset(service_id) - Direct connection to toolset (compatibility mode)

    Examples:
        ```python
        # Method 1: From endpoint instance (recommended)
        proxy = ToolsetProxy.from_endpoint(endpoint_service, "python_interpreter")

        # Method 2: From endpoint_id string (lazy connection)
        proxy = ToolsetProxy.from_endpoint(endpoint_id, "python_interpreter")

        # Method 3: From toolset service_id (legacy compatibility)
        proxy = ToolsetProxy.from_toolset(toolset_service_id)

        # Use proxy
        tools = await proxy.list_tools()
        result = await proxy.invoke("execute_code", {"code": "print('hello')"})
        ```
    """

    def __init__(
        self,
        mode: ProxyMode,
        toolset_name: str,
        cache_ttl: int = 300,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
        **kwargs
    ):
        """Initialize ToolsetProxy (private, use factory methods instead).

        Args:
            mode: Connection mode (ENDPOINT_ROUTED or DIRECT_CONNECTION)
            toolset_name: Toolset name or service_id
            cache_ttl: Cache time-to-live in seconds (default: 300)
            max_retries: Maximum number of retries on failure (default: 3)
            retry_delay_base: Base delay for exponential backoff (default: 1.0)
            **kwargs: Mode-specific parameters
        """
        self.mode = mode
        self.toolset_name = toolset_name
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self.retry_delay_base = retry_delay_base

        # Mode-specific attributes
        if mode == ProxyMode.ENDPOINT_ROUTED:
            self.endpoint = kwargs.get('endpoint')
            self.endpoint_id = kwargs.get('endpoint_id')
        else:  # DIRECT_CONNECTION
            self.service_id = kwargs.get('service_id')
            self._connected = False

        # Cache
        self._function_descriptions_cache: Optional[CachedFunctionDescriptions] = None
        self._lock = asyncio.Lock()

        logger.debug(
            f"ToolsetProxy initialized: mode={self.mode.value}, "
            f"toolset={toolset_name}"
        )

    @classmethod
    def from_endpoint(
        cls,
        endpoint,
        toolset_name: str,
        cache_ttl: int = 300,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
    ):
        """Create ToolsetProxy that routes through endpoint (recommended).

        This mode provides automatic fault recovery when toolset restarts.

        Args:
            endpoint: Endpoint service/RemoteService instance or endpoint service_id string.
                - If service/instance: Uses directly
                - If service_id string: Connects on first use (lazy connection)
            toolset_name: Name of the toolset (e.g., "python_interpreter")
            cache_ttl: Cache TTL in seconds (default: 300)
            max_retries: Max retry count (default: 3)
            retry_delay_base: Retry delay base (default: 1.0)

        Returns:
            ToolsetProxy instance in ENDPOINT_ROUTED mode

        Example:
            ```python
            # With endpoint instance
            proxy = ToolsetProxy.from_endpoint(endpoint_service, "python_interpreter")

            # With endpoint_id (lazy connection)
            proxy = ToolsetProxy.from_endpoint(endpoint_id, "python_interpreter")
            ```
        """
        # Check if endpoint is a string (service_id) or an instance
        if isinstance(endpoint, str):
            # endpoint_id provided - lazy connection
            return cls(
                mode=ProxyMode.ENDPOINT_ROUTED,
                toolset_name=toolset_name,
                cache_ttl=cache_ttl,
                max_retries=max_retries,
                retry_delay_base=retry_delay_base,
                endpoint_id=endpoint,
            )
        else:
            # endpoint instance provided - use directly
            return cls(
                mode=ProxyMode.ENDPOINT_ROUTED,
                toolset_name=toolset_name,
                cache_ttl=cache_ttl,
                max_retries=max_retries,
                retry_delay_base=retry_delay_base,
                endpoint=endpoint,
            )

    @classmethod
    def from_toolset(
        cls,
        service_id: str,
        cache_ttl: int = 300,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
    ):
        """Create ToolsetProxy with direct connection to toolset.

        This mode bypasses endpoint and connects directly to toolset service_id.
        Less resilient to toolset restarts but useful for legacy compatibility.

        Args:
            service_id: Toolset service ID (not endpoint service_id!)
            cache_ttl: Cache TTL in seconds (default: 300)
            max_retries: Max retry count (default: 3)
            retry_delay_base: Retry delay base (default: 1.0)

        Returns:
            ToolsetProxy instance in DIRECT_CONNECTION mode

        Example:
            ```python
            proxy = ToolsetProxy.from_toolset(toolset_service_id)
            ```
        """
        return cls(
            mode=ProxyMode.DIRECT_CONNECTION,
            toolset_name=service_id,  # Use service_id as name for now
            cache_ttl=cache_ttl,
            max_retries=max_retries,
            retry_delay_base=retry_delay_base,
            service_id=service_id,
        )

    async def _ensure_connected(self):
        """Ensure connection is established."""
        if self.mode == ProxyMode.ENDPOINT_ROUTED:
            # Ensure endpoint connection
            if not hasattr(self, 'endpoint') or self.endpoint is None:
                if hasattr(self, 'endpoint_id'):
                    from ..remote import connect_remote
                    self.endpoint = await connect_remote(self.endpoint_id)
                    logger.debug(f"Connected to endpoint: {self.endpoint_id}")
                else:
                    raise RuntimeError("No endpoint or endpoint_id available")

        elif self.mode == ProxyMode.DIRECT_CONNECTION:
            # Ensure toolset connection
            if not self._connected:
                from ..remote import connect_remote
                self.toolset_service = await connect_remote(self.service_id)
                self._connected = True
                logger.debug(f"Connected to toolset service: {self.service_id}")

    async def list_tools(
        self,
        force_refresh: bool = False
    ) -> List[Dict]:
        """List all available tools from this toolset (with caching).

        This method:
        1. Checks cache validity (unless force_refresh=True)
        2. Returns cached tools if valid
        3. Fetches fresh tools (mode-dependent)
        4. Falls back to stale cache if fetch fails (degradation strategy)

        Args:
            force_refresh: Force refresh cache even if valid

        Returns:
            List of tool description dicts

        Raises:
            Exception: If fetch fails and no cache available
        """
        # Ensure connection for DIRECT_CONNECTION mode
        if self.mode == ProxyMode.DIRECT_CONNECTION:
            await self._ensure_connected()

        async with self._lock:
            # Check cache validity
            if not force_refresh and self._is_cache_valid():
                logger.debug(
                    f"Using cached tools for {self.toolset_name} "
                    f"(age: {time.time() - self._function_descriptions_cache.timestamp:.1f}s)"
                )
                return self._function_descriptions_cache.descriptions

            # Fetch fresh tools (mode-dependent)
            try:
                logger.info(f"Fetching tools for {self.toolset_name} (mode: {self.mode.value})")

                if self.mode == ProxyMode.ENDPOINT_ROUTED:
                    # Route through endpoint
                    result = await self.endpoint.invoke(
                        "list_toolset_tools",
                        {"toolset_name": self.toolset_name}
                    )
                else:
                    # Direct connection to toolset
                    result = await self.toolset_service.invoke("list_tools", {})

                if result.get("success"):
                    descriptions = result.get("tools", [])

                    # Update cache
                    self._function_descriptions_cache = CachedFunctionDescriptions(
                        descriptions=descriptions,
                        timestamp=time.time(),
                        version=result.get("version")
                    )

                    logger.debug(
                        f"Cached {len(descriptions)} tools "
                        f"for {self.toolset_name}"
                    )
                    return descriptions
                else:
                    error = result.get("error", "Unknown error")
                    raise Exception(
                        f"Failed to list tools: {error}"
                    )

            except Exception as e:
                # Degradation strategy: use stale cache if available
                if self._function_descriptions_cache:
                    cache_age = time.time() - self._function_descriptions_cache.timestamp
                    logger.warning(
                        f"Failed to refresh tools for {self.toolset_name}, "
                        f"using stale cache (age: {cache_age:.1f}s): {e}"
                    )
                    return self._function_descriptions_cache.descriptions
                else:
                    raise Exception(
                        f"Failed to list tools and no cache available: {e}"
                    )

    def _is_cache_valid(self) -> bool:
        """Check if cache is valid (not expired).

        Returns:
            True if cache exists and not expired, False otherwise
        """
        if not self._function_descriptions_cache:
            return False

        age = time.time() - self._function_descriptions_cache.timestamp
        return age < self.cache_ttl

    def invalidate_cache(self):
        """Invalidate cached tools.

        This forces a fresh fetch on next list_tools() call.
        Useful when toolset is updated or restarted.
        """
        if self._function_descriptions_cache:
            logger.debug(f"Invalidating cache for {self.toolset_name}")
        self._function_descriptions_cache = None

    async def invoke(self, method_name: str, args: Dict = None) -> Dict:
        """Invoke a toolset method (mode-dependent).

        This method NEVER raises exceptions - it always returns a dict.
        On failure, it returns an error dict that LLM can understand.

        This method:
        1. Routes call based on proxy mode (ENDPOINT_ROUTED or DIRECT_CONNECTION)
        2. Invalidates cache on certain failures (toolset unavailable)
        3. Uses exponential backoff for retries
        4. Returns friendly error messages (never raises exceptions)

        Args:
            method_name: Name of the method to invoke
            args: Method arguments (default: {})

        Returns:
            Method result dict (success) or error dict (failure)
            Error dict format: {
                "success": False,
                "error": "Human-readable error message",
                "error_type": "ExceptionType",
                "toolset": "toolset_name"
            }

        Example:
            ```python
            result = await proxy.invoke("execute_code", {
                "code": "print('hello')",
                "timeout": 10
            })
            if result.get("success", True):
                print("Success:", result)
            else:
                print("Error:", result["error"])
            ```
        """
        args = args or {}

        # Wrap everything in try-except to ensure we never raise
        try:
            # Ensure connection for DIRECT_CONNECTION mode
            if self.mode == ProxyMode.DIRECT_CONNECTION:
                await self._ensure_connected()

            for attempt in range(self.max_retries):
                try:
                    logger.debug(
                        f"Invoking {self.toolset_name}.{method_name} "
                        f"(attempt {attempt + 1}/{self.max_retries}, mode: {self.mode.value})"
                    )

                    # Mode-dependent call
                    if self.mode == ProxyMode.ENDPOINT_ROUTED:
                        # Route through endpoint
                        result = await self.endpoint.invoke(
                            "proxy_toolset",
                            {
                                "method_name": method_name,
                                "args": args,
                                "toolset_name": self.toolset_name
                            }
                        )
                    else:
                        # Direct connection to toolset
                        result = await self.toolset_service.invoke(method_name, args)

                    # Success (assuming success=True or no error field)
                    if result.get("success", True):
                        return result

                    # Failure - analyze error
                    error = result.get("error", "Unknown error")
                    logger.warning(
                        f"Toolset call failed: {self.toolset_name}.{method_name} - {error}"
                    )

                    # Check if it's a recoverable error
                    if self._is_recoverable_error(error):
                        # Invalidate cache (toolset might be restarting)
                        self.invalidate_cache()

                        # Retry if not last attempt
                        if attempt < self.max_retries - 1:
                            delay = self.retry_delay_base * (2 ** attempt)  # Exponential backoff
                            logger.info(
                                f"Retrying {self.toolset_name}.{method_name} "
                                f"after {delay}s delay..."
                            )
                            await asyncio.sleep(delay)
                            continue

                    # Non-recoverable error or last attempt
                    # Ensure error dict has standard format
                    return {
                        "success": False,
                        "error": f"Tool '{method_name}' execution failed: {error}",
                        "error_type": "ToolExecutionError",
                        "toolset": self.toolset_name,
                    }

                except Exception as e:
                    logger.error(
                        f"Error invoking {self.toolset_name}.{method_name}: {e}",
                        exc_info=True
                    )

                    # Last attempt, return error dict
                    if attempt == self.max_retries - 1:
                        return {
                            "success": False,
                            "error": f"Tool '{method_name}' failed: {str(e)}",
                            "error_type": type(e).__name__,
                            "toolset": self.toolset_name,
                        }

                    # Invalidate cache and retry
                    self.invalidate_cache()
                    delay = self.retry_delay_base * (2 ** attempt)
                    logger.info(f"Retrying after {delay}s delay...")
                    await asyncio.sleep(delay)

            # Should not reach here, but just in case
            return {
                "success": False,
                "error": f"Tool '{method_name}' failed after {self.max_retries} retries",
                "error_type": "MaxRetriesExceeded",
                "toolset": self.toolset_name,
            }

        except Exception as e:
            # Catch-all: ensure we NEVER raise exceptions
            logger.error(
                f"Unexpected error in invoke({method_name}): {e}",
                exc_info=True
            )
            return {
                "success": False,
                "error": f"Unexpected error calling tool '{method_name}': {str(e)}",
                "error_type": type(e).__name__,
                "toolset": self.toolset_name,
            }

    def _is_recoverable_error(self, error: str) -> bool:
        """Check if error is recoverable (worth retrying).

        Args:
            error: Error message

        Returns:
            True if error is recoverable, False otherwise
        """
        error_lower = error.lower()

        # Recoverable: toolset not found, unavailable, connection issues
        recoverable_keywords = [
            "not found",
            "unavailable",
            "connection",
            "timeout",
            "no instance",
        ]

        return any(keyword in error_lower for keyword in recoverable_keywords)

    def to_tool_wrapper(self) -> Callable:
        """Convert proxy to a callable tool wrapper for Agent use.

        Returns:
            Async callable that invokes methods through this proxy

        Example:
            ```python
            wrapper = proxy.to_tool_wrapper()
            result = await wrapper("execute_code", code="print('hello')")
            ```
        """
        async def wrapper(method_name: str, **kwargs):
            return await self.invoke(method_name, kwargs)

        # Add metadata for inspection
        wrapper.__toolset_name__ = self.toolset_name
        wrapper.__proxy__ = self

        return wrapper

    def __repr__(self) -> str:
        """String representation."""
        cache_status = "cached" if self._is_cache_valid() else "no cache"
        return f"ToolsetProxy(toolset={self.toolset_name}, {cache_status})"
