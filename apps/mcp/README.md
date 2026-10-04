# MCPGateway

Expose configured MCP servers through one managed HTTP gateway, with controls for their lifecycle.

## Using this App

Configure servers in the workspace, then use `list_servers` to inspect their state. `start_servers`, `stop_servers` and `restart_server` manage selected servers. `get_uri` returns the gateway address for clients; each mounted server’s tools are namespaced by the gateway.

`add_server` and `remove_server` change the configured pool. Each server still needs its own executable, network access and credentials. This package is a backend service, not a separate MCP client window.

## Agent interface

Available tools: `add_server`, `get_server`, `get_uri`, `list_servers`, `remove_server`, `restart_server`, `start_servers`, `stop_servers`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).

## Prepared Fleet tools for the independent Agent App

The same `mcp-gateway` App sources also provide a scoped Fleet entry. Its release
contains a reviewed tool contract; its server connections come from ordinary
prepared App configuration. It does not expose server management or a gateway
URI to consumers. The existing `MCPGatewayToolSet`, CLI and Desktop entry are
unchanged.

Build a release for a selected node platform:

```sh
python -m pantheon.platform.mcp_package \
  --output /absolute/path/to/mcp-app \
  --platform darwin-arm64 \
  --exports /absolute/path/to/reviewed-exports.json \
  --credential-slot docs_api
```

Omit `--credential-slot` for an unauthenticated server. The reviewed exports
file maps ordinary RPC method names to particular upstream tools, for example:

```json
{
  "search_docs": {
    "server": "docs",
    "tool": "search",
    "description": "Search the selected documentation server",
    "parameters": {
      "type": "object",
      "properties": {"query": {"type": "string"}},
      "required": ["query"],
      "additionalProperties": false
    }
  }
}
```

Copy the complete upstream `inputSchema`, including titles, definitions and
constraints, into `parameters`. Startup verifies that every exported tool still
has the reviewed schema (an omitted `additionalProperties` is normalized to
false). Changes require reviewing and publishing a new contract. External schema
references and schema base URI changes are rejected. Discovery may see other
upstream tools, but only the declared exports become RPC methods.

Use the generated `tool-functions.json` as the `functions` of an Agent dependency
profile under `mcp_servers`. Its `alias` names the ordinary issued App dependency.
The dependency grant must authorize the selected RPC methods and caller arguments;
this schema file is not an authorization credential. The App package works with
the existing App artifact, deployment and dependency machinery; it needs no new
Agent transport or lifecycle controller. As with other native Apps, the process
runs within the selected node user's trust boundary.

The prepared backend values for the HTTP example are:

```json
{
  "mcp": {
    "protocol": 1,
    "servers": {
      "docs": {
        "transport": "http",
        "url": "https://docs.example/mcp",
        "credential": "docs_api"
      }
    }
  }
}
```

`docs_api` is a Fleet vault credential slot whose endpoint must exactly match
`url`; the key is sent as a Bearer token. Put the vault reference in the ordinary
App deployment configuration, never the raw key in the release, values or Agent
profile. Only HTTP(S) endpoints without userinfo, query or fragment are accepted.
The HTTP client ignores ambient proxy settings and refuses redirects.

A stdio server instead uses:

```json
{
  "transport": "stdio",
  "command": ["/absolute/path/to/python", "/absolute/path/to/server.py"],
  "cwd": "/absolute/path/to/workspace",
  "env": {}
}
```

Commands are argv arrays, not shell strings. Executable and working-directory
paths belong to the selected node. Supply nonsecret environment configuration
explicitly; the App never copies the Agent environment. The MCP SDK's standard
OS environment allowlist still applies. This entry does not yet deliver stdio
secrets from vault slots, OAuth, legacy SSE transports, MCP sampling callbacks,
roots, resources, prompts or elicitation. Those configurations must retain the
legacy entry until their explicit migration is implemented; do not automatically
convert them or drop their capabilities.

Each App instance owns its MCP sessions and stdio children. Repeated calls reuse
the same live sessions. Shared state therefore belongs to that App instance:
choose separate provider instances/scopes when consumers require isolation.
The App does not infer per-Agent sessions or convert a stateful MCP server into
a stateless shared service. It allows eight concurrent calls with bounded queued
admission, validates arguments, and preserves MCP content, structured output and
`isError`. Responses exceeding the ordinary dependency payload budget fail
explicitly. Calls are not automatically replayed on transport failure or caller
cancellation; an unknown outcome requires inspection. Stop rejects new calls,
drains admitted work, then closes sessions and child processes.

This is a tested provider entry, not automatic migration of existing `mcp.json`
or a production rollout. `tests/test_scoped_mcp_app.py` covers real in-process,
HTTP and subprocess MCP sessions, an isolated ordinary App HTTP host and the
Agent dependency consumer. The Agent test uses an in-process RPC link; it does
not claim a live enrolled Fleet or cross-node acceptance.
