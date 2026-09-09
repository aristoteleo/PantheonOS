"""ToolsetProxy — the client half of an App instance's tool face.

Post-endpoint form: exactly one mode remains — dial the instance's own bus
service by service_id (what used to be ProxyMode.TOOLSET_ID). The endpoint
routing modes died with pantheon/endpoint/.

Keeps what mattered from the old proxy: instance pooling (one proxy — and
one negotiated connection — per service_id) and lazy connection.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from pantheon.utils.log import logger
from pantheon.utils.misc import wire_safe_tool_args

if TYPE_CHECKING:
    from pantheon.remote import RemoteService
    from pantheon.apps.resolver import AppInstanceResolver


# Metadata has separate read and recovery budgets. Normal invoke execution
# deadlines stay with RemoteService/the caller, including after recovery.
TOOLSET_SCHEMA_READ_TIMEOUT = 15.0
TOOLSET_RECOVERY_TIMEOUT = 45.0
TOOLSET_DISCOVERY_TIMEOUT = 2 * TOOLSET_SCHEMA_READ_TIMEOUT + TOOLSET_RECOVERY_TIMEOUT


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
        self._instance_binding: tuple[AppInstanceResolver, str, str, str] | None = None
        self._recovery_generation = 0
        self._recovery_task: asyncio.Task | None = None
        self._recovery_waiters = 0
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

    def bind_instance(
        self, resolver: "AppInstanceResolver", service_type: str, *,
        scope: str = "app", workdir: str | None = None,
    ) -> "ToolsetProxy":
        """Keep the local ensure coordinates for this exact pooled service.

        These objects never enter RPC arguments. Do not let a later caller
        silently rebind a service id to another user, resolver, or scope.
        """
        if service_type == "desktop":
            scope, workdir = "app", resolver._workdir
        binding = (resolver, service_type, scope, workdir or resolver._workdir)
        if self._instance_binding is not None and self._instance_binding != binding:
            raise ValueError("ToolsetProxy already has a different instance binding")
        self._instance_binding = binding
        return self

    @property
    def has_instance_binding(self) -> bool:
        """Whether this proxy owns its bounded App recovery (for outer callers)."""
        return self._instance_binding is not None

    @staticmethod
    def _has_no_responders(error: Exception) -> bool:
        from nats.errors import NoRespondersError

        return isinstance(error.__cause__ or error, NoRespondersError)

    async def _recover_instance(self, observed_generation: int) -> None:
        # A slower call can still be finishing its initial retries after another
        # call restored the App. Reuse that result instead of restarting again.
        if observed_generation != self._recovery_generation:
            return
        task = self._recovery_task
        if task is None:
            task = asyncio.create_task(self._restart_instance())
            self._recovery_task = task
        self._recovery_waiters += 1
        try:
            await asyncio.shield(task)
        finally:
            self._recovery_waiters -= 1
            if not self._recovery_waiters:
                if self._recovery_task is task:
                    self._recovery_task = None
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _restart_instance(self) -> None:
        resolver, service_type, scope, workdir = self._instance_binding
        logger.info(f"[apps] recovering unavailable '{service_type}' instance (scope={scope})")
        async with asyncio.timeout(TOOLSET_RECOVERY_TIMEOUT):
            resolver.invalidate(service_type, scope=scope)
            service_id = await resolver.ensure_instance(
                service_type, scope=scope, workdir=workdir,
            )
            if service_id != self.service_id:
                # A different subject would route the pending operation to a
                # different instance. Refuse, even if that instance is healthy.
                raise RuntimeError("Recovered App service id does not match the bound instance")
            self._recovery_generation += 1

    async def _ensure_connected(self):
        if self.service is None:
            from pantheon.remote import connect_remote

            self.service = await connect_remote(self.service_id)
            logger.debug(f"Connected to toolset service: {self.service_id}")

    async def list_tools(self) -> dict:
        """Read metadata, allowing a separate bounded App recovery if missing."""
        result = await self._invoke("list_tools", {}, read_timeout=TOOLSET_SCHEMA_READ_TIMEOUT)
        if result.get("success"):
            return result
        raise Exception(f"Failed to list tools: {result.get('error', 'Unknown error')}")

    async def invoke(self, method_name: str, args: Optional[Dict] = None) -> Dict:
        """Invoke a tool, recovering its exact App only if nobody received it.

        A NoResponders error is safe to retry: the request reached nobody.
        Timeouts and remote method errors are never replayed. After the usual
        startup retries, a bound App may be ensured once on the current node;
        the original method then receives at most one more retry round.
        """
        return await self._invoke(method_name, args)

    async def _invoke(self, method_name: str, args: Optional[Dict], *,
                      read_timeout: float | None = None) -> Dict:
        safe_args = wire_safe_tool_args(args or {})
        if self._instance_binding and self._instance_binding[1] == "file_manager":
            from pantheon.internal.memory_system.file_routing import route_memory_file
            local = await route_memory_file(method_name, safe_args, workdir=self._instance_binding[3])
            if local is not None:
                return local
        generation = self._recovery_generation
        for round_index in range(2):
            try:
                # None leaves normal tool execution deadlines unchanged. For
                # metadata this bounds the ENTIRE round, not each attempt.
                async with asyncio.timeout(read_timeout):
                    await self._ensure_connected()
                    return await self._invoke_startup_retries(method_name, safe_args)
            except Exception as e:
                if (round_index or self._instance_binding is None
                        or not self._has_no_responders(e)):
                    raise
                await self._recover_instance(generation)

    async def _invoke_startup_retries(self, method_name: str, safe_args: Dict) -> Dict:
        delay = 0.5
        for attempt in range(6):
            try:
                return await self.service.invoke(method_name, safe_args)
            except Exception as e:
                if not self._has_no_responders(e) or attempt == 5:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)

    def __repr__(self) -> str:
        return f"ToolsetProxy(service_id={self.service_id[:12]}…)"
