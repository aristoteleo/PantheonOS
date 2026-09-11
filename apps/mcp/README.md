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
