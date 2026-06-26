---
id: proto_mcp_http
name: Proto — raw MCP-over-HTTP protocol
description: |
  Reference for talking to the Proto MCP server with raw HTTP (curl / any
  language) or an MCP SDK, in case the bundled proto_client.py can't be used.
tags: [proto, mcp, http, jsonrpc, reference]
---

# Proto MCP over raw HTTP

The Proto MCP server speaks the **MCP Streamable HTTP** transport: JSON-RPC 2.0
over a single endpoint. Use this only if `proto_client.py` doesn't fit; that
client already implements everything below.

- **Endpoint:** `https://mcp.evodesign.org/mcp`
- **Auth:** header `Authorization: Bearer $PROTO_API_KEY`
- **Required request headers:** `Content-Type: application/json`,
  `Accept: application/json, text/event-stream`
- **Responses:** either `application/json` (one JSON-RPC object) or
  `text/event-stream` (SSE — the JSON-RPC response is the last `data:` line
  with a `result`/`error`). Always handle both.

## Handshake → call sequence

1. **initialize** (POST). The response includes an `Mcp-Session-Id` header —
   send it on every subsequent request.
   ```json
   {"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2025-06-18","capabilities":{},
              "clientInfo":{"name":"client","version":"1.0"}}}
   ```
2. **notifications/initialized** (POST, no `id`; expect `202 Accepted`, no body)
   ```json
   {"jsonrpc":"2.0","method":"notifications/initialized"}
   ```
3. **tools/list** (POST) — enumerate tools.
   ```json
   {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
   ```
4. **tools/call** (POST) — call a tool. The result is in
   `result.content[].text` (often JSON-as-text) and/or `result.structuredContent`.
   ```json
   {"jsonrpc":"2.0","id":3,"method":"tools/call",
    "params":{"name":"list_tools","arguments":{}}}
   ```

## curl example

```bash
# 1. initialize — grab the session id from the response headers
SID=$(curl -sD - -o /dev/null https://mcp.evodesign.org/mcp \
  -H "Authorization: Bearer $PROTO_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
  | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')

# 2. complete the handshake
curl -s https://mcp.evodesign.org/mcp \
  -H "Authorization: Bearer $PROTO_API_KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. call a tool
curl -s https://mcp.evodesign.org/mcp \
  -H "Authorization: Bearer $PROTO_API_KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
```

## Using an MCP SDK instead

If an MCP SDK is available, prefer it over hand-rolling the transport.

```python
# Python — fastmcp (a Pantheon dependency)
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import os

transport = StreamableHttpTransport(
    "https://mcp.evodesign.org/mcp",
    headers={"Authorization": f"Bearer {os.environ['PROTO_API_KEY']}"},
)
async def main():
    async with Client(transport) as c:
        print(await c.list_tools())
        print(await c.call_tool("whoami", {}))
```

```python
# Python — official mcp SDK
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
import os

async def main():
    headers = {"Authorization": f"Bearer {os.environ['PROTO_API_KEY']}"}
    async with streamablehttp_client("https://mcp.evodesign.org/mcp", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print(await s.call_tool("whoami", {}))
```

## Prompts & resources (optional)

The server also exposes MCP **prompts** as slash-style helpers
(`find_tool`, `tool_walkthrough`) and **resources** by URI
(`proto-tools://tools/{key}`, `bio://constraints/{key}`) — reachable via the
standard `prompts/get` and `resources/read` JSON-RPC methods.
