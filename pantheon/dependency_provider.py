"""Agent tools carried by an explicitly granted App dependency.

The composition root supplies the *caller-visible* function schemas, with
session/workspace arguments already removed. The gateway remains authoritative
for method/argument permissions and injects its bound arguments. No discovery,
provider recovery, URI handoff, ambient context or credential fallback occurs.
"""
import asyncio
import copy
import json
import re
from collections.abc import Sequence
from typing import Any

from .agent import ToolInfo, ToolProvider
from .apps.dependency_client import DependencyClient, DependencyCallError


_RPC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")
_ARGUMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_PRIVATE_ARGUMENTS = {"context_variables", "_call_agent", "_background"}


async def _drain_call(task: asyncio.Task):
    """Cancellation does not abandon an accepted blocking RPC in a thread.

    Wait until the transport returns (possibly with an unknown remote outcome),
    then propagate cancellation. Repeated cancellation cannot detach the worker.
    This is not a promise that a timed-out remote mutation has stopped.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()  # Consume an error without leaking response data.
        raise asyncio.CancelledError
    return task.result()


class DependencyToolProvider(ToolProvider):
    """A deployment-owned, bounded transport with locally available schemas.

    ``functions`` are OpenAI function descriptors (name/description/parameters),
    not the surrounding {type, function} wrapper. They describe only arguments
    the caller may send. Permissions and session ownership are never inferred
    from these descriptors; those are enforced by the issued dependency grant.
    """

    def __init__(self, name: str, client: DependencyClient, functions: Sequence[dict],
                 *, timeout_seconds: int = 60, max_inflight: int = 8):
        if (not isinstance(name, str) or not _RPC_NAME.fullmatch(name) or "__" in name
                or not isinstance(client, DependencyClient)
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600
                or type(max_inflight) is not int or not 1 <= max_inflight <= 64):
            raise ValueError("Invalid dependency tool binding")
        self.toolset_name = name
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._tools = self._validate_functions(functions)
        self._slots = asyncio.Semaphore(max_inflight)
        self._pending: set[asyncio.Task] = set()
        self._closed = False

    @staticmethod
    def _validate_functions(functions):
        try:
            encoded = json.dumps(functions, allow_nan=False)
            if len(encoded.encode()) > 512 * 1024:
                raise ValueError
            functions = json.loads(encoded)
            if not isinstance(functions, list) or not 1 <= len(functions) <= 64:
                raise ValueError
            tools = {}
            for function in functions:
                name = function["name"]
                parameters = function["parameters"]
                properties = parameters["properties"]
                required = parameters.get("required", [])
                if (not isinstance(name, str) or not _RPC_NAME.fullmatch(name) or name in tools
                        or not isinstance(function.get("description", ""), str)
                        or parameters.get("type") != "object" or not isinstance(properties, dict)
                        or len(properties) > 64 or properties.keys() & _PRIVATE_ARGUMENTS
                        or not all(_ARGUMENT_NAME.fullmatch(key) and isinstance(value, dict)
                                   for key, value in properties.items())
                        or not isinstance(required, list) or len(set(required)) != len(required)
                        or not set(required) <= properties.keys()
                        or parameters.get("additionalProperties", False) is not False):
                    raise ValueError
                parameters["additionalProperties"] = False
                tools[name] = function
            return tools
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise ValueError("Invalid dependency tool schemas") from None

    async def initialize(self):
        self._check_open()

    def _check_open(self):
        if self._closed:
            raise RuntimeError("Dependency tool binding is closed")

    async def list_tools(self) -> list[ToolInfo]:
        self._check_open()
        # Agent's model-schema conversion adds prefixes/background parameters.
        # Never allow those changes to mutate the binding's admission rules.
        return [ToolInfo(name=name, description=f.get("description", ""), inputSchema=copy.deepcopy(f))
                for name, f in self._tools.items()]

    async def call_tool(self, name: str, args: dict) -> Any:
        self._check_open()
        function = self._tools.get(name) if isinstance(name, str) else None
        if function is None or not isinstance(args, dict):
            raise ValueError("Tool is not available in this dependency binding")
        parameters = function["parameters"]
        if (not args.keys() <= parameters["properties"].keys()
                or not set(parameters.get("required", [])) <= args.keys()):
            raise ValueError("Arguments are not available in this dependency binding")
        try:
            encoded = json.dumps(args, allow_nan=False)
            if len(encoded.encode()) > 512 * 1024:
                raise ValueError
            arguments = json.loads(encoded)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Invalid dependency tool arguments") from None
        async with self._slots:
            self._check_open()  # A queued call must not start after shutdown.
            task = asyncio.create_task(asyncio.to_thread(
                self._client.invoke, name, arguments, timeout_seconds=self._timeout_seconds))
            self._pending.add(task)
            try:
                response = await _drain_call(task)
                # Fleet forwards the ordinary portable App RPC envelope. Keep
                # the ToolProvider contract (the tool's own return value), and
                # never misreport a backend exception as a successful result.
                # An exception can follow a partial mutation, hence unknown.
                if (not isinstance(response, dict) or response.get("success") is not True
                        or "result" not in response):
                    raise DependencyCallError("Dependency tool failed; outcome may be unknown",
                                              outcome_unknown=True)
                return response["result"]
            finally:
                self._pending.discard(task)

    async def shutdown(self):
        self._closed = True
        # Calls remove themselves only after their transport actually returns.
        # Shield/drain each one even if the shutdown waiter is cancelled again.
        async def drain():
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        await _drain_call(asyncio.create_task(drain()))
