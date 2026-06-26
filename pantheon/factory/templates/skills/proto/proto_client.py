"""Portable Proto MCP-over-HTTP client.

Talks to the hosted Proto generative-biology MCP server using nothing but
``httpx`` — no MCP SDK required — so this file travels with the skill folder
and runs anywhere. It implements the MCP *Streamable HTTP* transport
(JSON-RPC 2.0 over a single ``/mcp`` endpoint, responses as JSON or SSE).

Auth is a Proto API key sent as a Bearer token. Get one at
https://proto.evodesign.org -> Settings -> API keys, then::

    export PROTO_API_KEY="sk-..."

Usage::

    from proto_client import ProtoMCP

    with ProtoMCP() as proto:                 # reads PROTO_API_KEY from env
        print(proto.call("whoami"))           # account + budget
        print(proto.list_tools())             # MCP tools exposed
        cat = proto.call("list_tools")        # Proto's design tool catalog
        schema = proto.call("get_tool_schema", {"key": "<tool key>"})
        run = proto.call("create_run", {"program": {...}})
        proto.call("get_run_status", {"run_id": run["run_id"]})

`call(name, arguments)` returns the tool's structured result (parsed JSON when
the tool returns JSON text, otherwise the raw text). Discover exact argument
and field names at runtime with ``get_tool_schema`` / ``validate_program`` —
do not hard-code Proto's program schema.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_URL = os.environ.get("PROTO_MCP_URL", "https://mcp.evodesign.org/mcp")
PROTOCOL_VERSION = "2025-06-18"


class ProtoMCPError(RuntimeError):
    """A Proto MCP call failed (transport, auth, or a JSON-RPC error)."""


class ProtoMCP:
    """Minimal synchronous MCP Streamable-HTTP client for the Proto server."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        url: str = DEFAULT_URL,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("PROTO_API_KEY")
        if not self.api_key:
            raise ProtoMCPError(
                "No Proto API key. Set PROTO_API_KEY (get one at "
                "proto.evodesign.org -> Settings -> API keys)."
            )
        self.url = url
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        self._session_id: Optional[str] = None
        self._rpc_id = 0
        self._initialize()

    # -- internals -----------------------------------------------------------

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _post(self, payload: Dict[str, Any], notification: bool = False) -> Optional[Dict[str, Any]]:
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = self._http.post(self.url, json=payload, headers=headers)
        # The server assigns a session id on initialize; reuse it thereafter.
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if notification:
            return None  # notifications get 202 Accepted with no JSON-RPC body
        resp.raise_for_status()
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Dict[str, Any]:
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            # Server-Sent Events: take the last `data:` line that is a
            # JSON-RPC response (skip progress/notification events).
            found: Optional[Dict[str, Any]] = None
            for line in resp.text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    found = obj
            if found is None:
                raise ProtoMCPError(f"No JSON-RPC response in SSE stream: {resp.text[:500]}")
            return found
        return resp.json()

    def _initialize(self) -> None:
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pantheon-proto-skill", "version": "1.0.0"},
                },
            }
        )
        if resp and "error" in resp:
            raise ProtoMCPError(f"initialize failed: {resp['error']}")
        # Required handshake completion.
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)

    # -- public API ----------------------------------------------------------

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return the MCP tools the Proto server exposes (name + input schema)."""
        resp = self._post(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        )
        if "error" in resp:
            raise ProtoMCPError(resp["error"])
        return resp["result"].get("tools", [])

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a Proto MCP tool. Returns parsed structured/JSON output, else text."""
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if "error" in resp:
            raise ProtoMCPError(f"{name} failed: {resp['error']}")
        result = resp.get("result", {})
        # Prefer the server's structured output if present.
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        # Otherwise join text blocks and try to decode JSON.
        text = "\n".join(
            b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"
        )
        if result.get("isError"):
            raise ProtoMCPError(f"{name} returned an error: {text}")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ProtoMCP":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


if __name__ == "__main__":
    # Smoke test: prints account info + the Proto tool catalog.
    with ProtoMCP() as proto:
        print("whoami:", json.dumps(proto.call("whoami"), indent=2, default=str)[:1000])
        print("\nMCP tools:", [t.get("name") for t in proto.list_tools()])
