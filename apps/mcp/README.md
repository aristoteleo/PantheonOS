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
secrets from vault slots, OAuth, legacy SSE transports,
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

### Model Service dependency for MCP sampling

MCP servers can request generation through an explicitly bound Model Service.
Add a `models` credential slot when building the package, then issue its ordinary
`model_services_control` dependency grant to **the MCP App instance**, separately
from the Agent's model grant. The credential is an endpoint-paired dependency RPC
reference, not a provider API key or Fleet/Hub owner key. Configure `mcp.sampling`:

```json
{
  "credential": "models",
  "model": "fleet-model://local-llm/example%3A8b",
  "max_tokens": 4096,
  "max_requests_per_call": 2
}
```

An exact `fleet-route://...` reference is also supported. Model preferences sent
by the upstream MCP server cannot change this owner binding. The existing Model
Services client retains catalog/capability checks, route policy, scoped grants,
Connector streaming, cancellation and revocation. Sampling does not construct an
Agent, load provider SDKs or read legacy API-key/model settings. This also keeps
Platform Budget behind its original Model Service publication and LiteLLM path.

Sampling preserves the supplied system prompt and user/assistant message history.
Text and inline image inputs are supported; Model Services must confirm vision
capability before images are sent. Temperature and stop sequences are forwarded
within validated limits, and requested output tokens are capped by the owner.
Audio, sampling tools/toolChoice and implicit `includeContext` are rejected
before inference. The App advertises no sampling-tools capability and never
silently runs a model-driven tool loop or fetches another App's context.

A server may sample only while one of its exported tool calls is executing.
Each admitted call contributes the configured request allowance; concurrent calls
on the same server share that bounded allowance because the callback protocol
has no trusted parent-call identity. At most eight samples are in flight. Invalid
requests, startup-time or idle server requests and exhausted allowances cannot
start inference. Accepted model requests are never automatically replayed.
Model errors sent to MCP contain no upstream exception details or credentials.

The built App includes the canonical lightweight Model Services client. Optional
`--transport /path/to/fleet-app-transport` bundles the target-platform workload
transport for direct connections. Without it, the client can use relay-allowed
placements and rejects direct-only ones; it never discovers a node-management
executable from PATH. Provider engines and the Agent runtime are not bundled.

`tests/test_scoped_mcp_sampling.py` exercises real MCP sampling, dependency RPC,
the original Connector and SSE, with controlled Hub/engine fixtures. A fresh
packaged process also refuses Agent, settings and provider SDK imports while its
stdio server successfully samples through that Connector. This is local
acceptance, not production enrollment, automatic `mcp.json` conversion or remote
GPU/Windows validation.
