"""ToolsetProxy — the client half of an App instance's tool face.

Post-endpoint form: exactly one mode remains — dial the instance's own bus
service by service_id (what used to be ProxyMode.TOOLSET_ID). The endpoint
routing modes died with pantheon/endpoint/.

Keeps what mattered from the old proxy: instance pooling (one proxy — and
one negotiated connection — per service_id) and lazy connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from pantheon.utils.log import logger
from pantheon.utils.misc import wire_safe_tool_args

if TYPE_CHECKING:
    from pantheon.remote import RemoteService


class ToolsetProxy:
    """Proxy for one App instance's tools, dialed directly by service_id."""

    _instance_pool: Dict[str, "ToolsetProxy"] = {}

    def __new__(cls, service_id: str, **kwargs):
        existing = cls._instance_pool.get(service_id)
        if existing is not None:
            return existing
        instance = super().__new__(cls)
        cls._instance_pool[service_id] = instance
        instance._is_initialized = False
        return instance

    def __init__(self, service_id: str, toolset_name: str | None = None):
        if self._is_initialized:
            return
        self.service_id = service_id
        self.toolset_name = toolset_name or service_id
        self.service: Optional[Any] = None
        self._is_initialized = True

    @classmethod
    def from_toolset(cls, service_or_id: Union[str, "RemoteService"]) -> "ToolsetProxy":
        """Proxy for an instance by service_id (or an already-open service)."""
        if isinstance(service_or_id, str):
            return cls(service_or_id)
        from pantheon.remote import RemoteService

        if isinstance(service_or_id, RemoteService):
            proxy = cls(service_or_id.service_id)
            proxy.service = service_or_id
            return proxy
        raise TypeError(
            f"service_or_id must be str or RemoteService, got {type(service_or_id)}"
        )

    async def _ensure_connected(self):
        if self.service is None:
            from pantheon.remote import connect_remote

            self.service = await connect_remote(self.service_id)
            logger.debug(f"Connected to toolset service: {self.service_id}")

    async def list_tools(self) -> dict:
        """The instance's visible tool face ({"success": True, "tools": [...]})."""
        result = await self.invoke("list_tools", {})
        if result.get("success"):
            return result
        raise Exception(f"Failed to list tools: {result.get('error', 'Unknown error')}")

    async def invoke(self, method_name: str, args: Optional[Dict] = None) -> Dict:
        """Invoke one tool. Args are stripped of unpicklable live objects."""
        await self._ensure_connected()
        return await self.service.invoke(method_name, wire_safe_tool_args(args or {}))

    def __repr__(self) -> str:
        return f"ToolsetProxy(service_id={self.service_id[:12]}…)"
