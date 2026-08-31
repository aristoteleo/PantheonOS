"""MCP Server Management Command Handler

Provides /mcp command for managing MCP servers in REPL.

Usage:
    /mcp                 - List all MCP servers and status
    /mcp start <name>    - Start an MCP server
    /mcp stop <name>     - Stop an MCP server
    /mcp restart <name>  - Restart an MCP server
    /mcp add <name> <cmd> [--autostart] - Add a new STDIO MCP server
    /mcp remove <name>   - Remove an MCP server
"""

from rich.console import Console
from typing import TYPE_CHECKING

from pantheon.repl.handlers.base import CommandHandler

if TYPE_CHECKING:
    from pantheon.repl.core import Repl


# Error indicators for filtering log lines
ERROR_INDICATORS = ["×", "Error", "error", "Failed", "failed", "╰─▶"]


class MCPCommandHandler(CommandHandler):
    """Handle /mcp commands for MCP server management."""
    
    def __init__(self, console: Console, parent: "Repl"):
        super().__init__(console, parent)
    
    def get_commands(self) -> list[tuple[str, str]]:
        """Return commands for autocomplete."""
        return [
            ("/mcp", "Manage MCP servers"),
            ("/mcp start", "Start MCP server"),
            ("/mcp stop", "Stop MCP server"),
            ("/mcp restart", "Restart MCP server"),
            ("/mcp add", "Add new MCP server"),
            ("/mcp remove", "Remove MCP server"),
        ]
    
    def match_command(self, command: str) -> bool:
        """Check if this handler matches the command."""
        cmd = command.strip().lower()
        return cmd == "/mcp" or cmd.startswith("/mcp ")
    
    async def handle_command(self, command: str) -> str | None:
        """Execute the MCP command."""
        import shlex
        try:
            parts = shlex.split(command.strip())
        except ValueError:
            # Fallback if shlex fails (unmatched quotes, etc.)
            parts = command.strip().split()
        
        # /mcp (no args) or /mcp list/status
        if len(parts) == 1:
            return await self._list_servers()
        
        action = parts[1].lower()
        
        if action in ("list", "status"):
            return await self._list_servers()
        elif action == "start":
            if len(parts) < 3:
                self.console.print("[yellow]Usage: /mcp start <server_name>[/yellow]")
                return None
            return await self._start_server(parts[2])
        elif action == "stop":
            if len(parts) < 3:
                self.console.print("[yellow]Usage: /mcp stop <server_name>[/yellow]")
                return None
            return await self._stop_server(parts[2])
        elif action == "restart":
            if len(parts) < 3:
                self.console.print("[yellow]Usage: /mcp restart <server_name>[/yellow]")
                return None
            return await self._restart_server(parts[2])
        elif action == "add":
            return await self._handle_add_command(parts[2:])
        elif action == "remove":
            if len(parts) < 3:
                self.console.print("[yellow]Usage: /mcp remove <server_name>[/yellow]")
                return None
            return await self._remove_server(parts[2])
        else:
            self.console.print(f"[red]Unknown action: {action}[/red]")
            self._print_help()
            return None
    
    def _print_help(self):
        """Print help for MCP commands."""
        self.console.print()
        self.console.print("[bold]MCP Server Management[/bold]")
        self.console.print()
        self.console.print("  /mcp                            List all MCP servers")
        self.console.print("  /mcp start <name>               Start a server")
        self.console.print("  /mcp stop <name>                Stop a server")
        self.console.print("  /mcp restart <name>             Restart a server")
        self.console.print("  /mcp remove <name>              Remove server config")
        self.console.print()
        self.console.print("[bold]Add Server:[/bold]")
        self.console.print("  /mcp add <name> <command>       Add STDIO server")
        self.console.print("  /mcp add <name> --uri <url>     Add HTTP server")
        self.console.print()
        self.console.print("[bold]Options:[/bold]")
        self.console.print("  --autostart                     Enable auto-start on REPL launch")
        self.console.print("  --desc '<description>'          Server description")
        self.console.print("  --env KEY=VALUE                 Environment variable (repeatable)")
        self.console.print()
        self.console.print("[bold]Examples:[/bold]")
        self.console.print("  /mcp add ctx7 'uvx context7'")
        self.console.print("  /mcp add ctx7 'uvx context7' --autostart --desc 'Context7 docs'")
        self.console.print("  /mcp add remote --uri http://localhost:3000/mcp")
        self.console.print("  /mcp add bio 'uvx biomcp' --env API_KEY=xxx --env DEBUG=1")
        self.console.print()
    
    async def _get_mcp_manager(self):
        """The mcp-gateway App instance, worn as the old manager interface.

        The endpoint's in-process manager is gone; the gateway runs as its
        own App and the REPL drives it over the bus through the chatroom's
        proxy. The adapter keeps this handler's call sites unchanged.
        """
        room = getattr(self.parent, "_chatroom", None)
        if room is None:
            return None
        return _GatewayClient(room)
    
    async def _list_servers(self) -> str | None:
        """List all MCP servers with status."""
        mcp_manager = await self._get_mcp_manager()
        if not mcp_manager:
            self.console.print("[red]MCP manager not available[/red]")
            return None
        
        result = await mcp_manager.list_services()
        services = result.get("services", [])
        
        self.console.print()
        self.console.print("[bold]MCP Servers[/bold]")
        self.console.print()
        
        if not services:
            self.console.print("[dim]  No MCP servers configured[/dim]")
            self.console.print()
            self.console.print("[dim]  Add a server with: /mcp add <name> <command>[/dim]")
        else:
            for svc in services:
                name = svc.get("name", "?")
                svc_type = svc.get("type", "?")
                status = svc.get("status", "unknown")
                desc = svc.get("description", "")
                logs = svc.get("logs", [])
                command = svc.get("command", "")
                # For HTTP type, use remote_uri (original config); for STDIO use gateway uri
                remote_uri = svc.get("remote_uri", "")
                
                # Status indicator
                if status == "running":
                    status_icon = "[green]●[/green]"
                    status_text = "[green]running[/green]"
                elif status == "starting":
                    status_icon = "[yellow]◐[/yellow]"
                    status_text = "[yellow]starting...[/yellow]"
                elif status == "stopped":
                    status_icon = "[dim]○[/dim]"
                    status_text = "[dim]stopped[/dim]"
                elif status == "error":
                    status_icon = "[red]✗[/red]"
                    status_text = "[red]error[/red]"
                else:
                    status_icon = "[yellow]?[/yellow]"
                    status_text = f"[yellow]{status}[/yellow]"
                
                # Main line: icon name (type) - status
                self.console.print(f"  {status_icon} [bold]{name}[/bold] [dim]({svc_type})[/dim] - {status_text}")
                
                # Show command (STDIO) or remote URI (HTTP)
                if command:
                    self.console.print(f"      [dim]cmd: {command}[/dim]")
                elif remote_uri:
                    self.console.print(f"      [dim]uri: {remote_uri}[/dim]")
                
                # Show description if present
                if desc:
                    self.console.print(f"      [dim]{desc}[/dim]")
                
                # Show logs for error status (filter for error lines)
                if status == "error" and logs:
                    error_lines = [
                        line for line in logs[-5:]
                        if any(ind in line for ind in ERROR_INDICATORS)
                    ][:3]  # Max 3 lines
                    for line in error_lines:
                        self.console.print(f"      [red dim]⚠ {line}[/red dim]")
                
                self.console.print()  # Blank line between servers
        
        # Show unified gateway info
        unified_uri = result.get("unified_uri", "")
        if unified_uri:
            self.console.print(f"[dim]Gateway: {unified_uri}[/dim]")
        
        self.console.print()
        return None
    
    async def _start_server(self, name: str) -> str | None:
        """Start an MCP server."""
        mcp_manager = await self._get_mcp_manager()
        if not mcp_manager:
            self.console.print("[red]MCP manager not available[/red]")
            return None
        
        self.console.print(f"[dim]Starting MCP server '{name}'...[/dim]")
        result = await mcp_manager.start_services([name])
        
        if not result.get("success"):
            errors = result.get("errors", [])
            self.console.print(f"[red]✗ Failed to start {name}[/red]")
            for err in errors:
                self.console.print(f"[dim]  {err}[/dim]")
        
        return None
    
    async def _stop_server(self, name: str) -> str | None:
        """Stop an MCP server."""
        mcp_manager = await self._get_mcp_manager()
        if not mcp_manager:
            self.console.print("[red]MCP manager not available[/red]")
            return None
        
        result = await mcp_manager.stop_services([name])
        
        if result.get("success"):
            self.console.print(f"[green]✅ Stopped: {name}[/green]")
        else:
            errors = result.get("errors", [])
            self.console.print(f"[red]✗ Failed to stop {name}[/red]")
            for err in errors:
                self.console.print(f"[dim]  {err}[/dim]")
        
        return None
    
    async def _restart_server(self, name: str) -> str | None:
        """Restart an MCP server."""
        mcp_manager = await self._get_mcp_manager()
        if not mcp_manager:
            self.console.print("[red]MCP manager not available[/red]")
            return None
        
        self.console.print(f"[dim]Restarting MCP server '{name}'...[/dim]")
        result = await mcp_manager.restart_service(name)
        
        if result.get("success"):
            self.console.print(f"[green]✅ Restarted: {name}[/green]")
        else:
            errors = result.get("errors", [])
            self.console.print(f"[red]✗ Failed to restart {name}[/red]")
            for err in errors:
                self.console.print(f"[dim]  {err}[/dim]")
        
        return None
    
    async def _handle_add_command(self, args: list) -> str | None:
        """Parse and execute add command with options.
        
        Supports:
        - STDIO: /mcp add <name> <command> [options]
        - HTTP:  /mcp add <name> --uri <url> [options]
        
        Options:
        - --autostart: Add to auto_start list
        - --desc '<description>': Server description
        - --env KEY=VALUE: Environment variable (repeatable)
        """
        if len(args) < 2:
            self.console.print("[yellow]Usage: /mcp add <name> <command|--uri url> [options][/yellow]")
            self.console.print()
            self.console.print("[dim]Options:[/dim]")
            self.console.print("[dim]  --autostart        Add to auto_start list[/dim]")
            self.console.print("[dim]  --desc 'text'      Server description[/dim]")
            self.console.print("[dim]  --env KEY=VALUE    Environment variable (repeatable)[/dim]")
            self.console.print("[dim]  --uri <url>        Use HTTP type instead of STDIO[/dim]")
            self.console.print()
            self.console.print("[dim]Examples:[/dim]")
            self.console.print("[dim]  /mcp add ctx7 'uvx context7'[/dim]")
            self.console.print("[dim]  /mcp add ctx7 'uvx context7' --autostart --desc 'Docs'[/dim]")
            self.console.print("[dim]  /mcp add remote --uri http://localhost:3000/mcp[/dim]")
            return None
        
        name = args[0]
        remaining = args[1:]
        
        # Parse options
        auto_start = False
        description = ""
        env = {}
        uri = None
        command_parts = []
        
        i = 0
        while i < len(remaining):
            arg = remaining[i]
            
            if arg == "--autostart":
                auto_start = True
            elif arg == "--uri" and i + 1 < len(remaining):
                i += 1
                uri = remaining[i]
            elif arg == "--desc" and i + 1 < len(remaining):
                i += 1
                description = remaining[i].strip("'\"")
            elif arg == "--env" and i + 1 < len(remaining):
                i += 1
                env_str = remaining[i]
                if "=" in env_str:
                    key, value = env_str.split("=", 1)
                    env[key] = value
            elif not arg.startswith("--"):
                command_parts.append(arg)
            
            i += 1
        
        # Determine type and validate
        if uri:
            # HTTP type
            return await self._add_server(
                name=name, 
                server_type="http",
                uri=uri, 
                description=description,
                auto_start=auto_start
            )
        else:
            # STDIO type
            command = " ".join(command_parts)
            
            if not command:
                self.console.print("[yellow]Missing command for STDIO server[/yellow]")
                self.console.print("[dim]Example: /mcp add myserver 'uvx my-mcp-server'[/dim]")
                return None
            
            return await self._add_server(
                name=name,
                server_type="stdio",
                command=command,
                env=env,
                description=description,
                auto_start=auto_start
            )
    
    async def _add_server(
        self, 
        name: str, 
        server_type: str = "stdio",
        command: str = "",
        uri: str = "",
        env: dict = None,
        description: str = "",
        auto_start: bool = False
    ) -> str | None:
        """Add a new MCP server (persisted to mcp.json).
        
        Args:
            name: Server name
            server_type: 'stdio' or 'http'
            command: Command to run (for STDIO)
            uri: URL (for HTTP)
            env: Environment variables (for STDIO)
            description: Server description
            auto_start: If True, add to auto_start list
        """
        mcp_manager = await self._get_mcp_manager()
        if not mcp_manager:
            self.console.print("[red]MCP manager not available[/red]")
            return None
        
        from pantheon.apps.builtin.mcp.manager import MCPServerConfig, MCPServerType
        
        try:
            if server_type == "http":
                config = MCPServerConfig(
                    name=name,
                    type=MCPServerType.HTTP,
                    uri=uri,
                    description=description,
                )
            else:
                config = MCPServerConfig(
                    name=name,
                    type=MCPServerType.STDIO,
                    command=command,
                    env=env or {},
                    description=description,
                )
            
            result = await mcp_manager.add_config(config, persist=True, auto_start=auto_start)
            
            if result.get("success"):
                self.console.print(f"[green]✅ Added: {name}[/green]")
                if server_type == "http":
                    self.console.print(f"[dim]  Type: HTTP, URI: {uri}[/dim]")
                else:
                    self.console.print(f"[dim]  Type: STDIO, Command: {command}[/dim]")
                    if env:
                        self.console.print(f"[dim]  Env: {', '.join(f'{k}={v}' for k, v in env.items())}[/dim]")
                if description:
                    self.console.print(f"[dim]  Description: {description}[/dim]")
                if result.get("persisted"):
                    self.console.print(f"[dim]  Saved to .pantheon/mcp.json[/dim]")
                if result.get("auto_start"):
                    self.console.print(f"[dim]  Added to auto_start list[/dim]")
                else:
                    self.console.print(f"[dim]  Use '/mcp start {name}' to start[/dim]")
            else:
                self.console.print(f"[red]✗ Failed to add: {result.get('message')}[/red]")
        except Exception as e:
            self.console.print(f"[red]✗ Invalid config: {e}[/red]")
        
        return None
    
    async def _remove_server(self, name: str) -> str | None:
        """Remove an MCP server configuration (persisted to mcp.json)."""
        mcp_manager = await self._get_mcp_manager()
        if not mcp_manager:
            self.console.print("[red]MCP manager not available[/red]")
            return None
        
        result = await mcp_manager.remove_config(name, persist=True)
        
        if result.get("success"):
            self.console.print(f"[green]✅ Removed: {name}[/green]")
            if result.get("persisted"):
                self.console.print(f"[dim]  Removed from .pantheon/mcp.json[/dim]")
        else:
            self.console.print(f"[red]✗ {result.get('message')}[/red]")
        
        return None


class _GatewayClient:
    """The old MCP manager interface, answered by the mcp-gateway App.

    Each method is one tool on the gateway instance; the chatroom proxy
    resolves and places the instance exactly as it does for agent calls.
    ``add_config`` flattens the MCPServerConfig dataclass to the wire.
    """

    def __init__(self, room):
        self._room = room

    async def _invoke(self, tool: str, args: dict | None = None) -> dict:
        res = await self._room.proxy_toolset(tool, args or {}, "mcp_gateway")
        if not isinstance(res, dict):
            return {"success": True, "result": res}
        return res

    async def list_services(self) -> dict:
        return await self._invoke("list_servers")

    async def start_services(self, names: list) -> dict:
        return await self._invoke("start_servers", {"names": list(names)})

    async def stop_services(self, names: list) -> dict:
        return await self._invoke("stop_servers", {"names": list(names)})

    async def restart_service(self, name: str) -> dict:
        return await self._invoke("restart_server", {"name": name})

    async def add_config(self, config, persist: bool = False, auto_start: bool = False) -> dict:
        from dataclasses import asdict

        return await self._invoke("add_server", {
            "config": asdict(config), "persist": persist, "auto_start": auto_start,
        })

    async def remove_config(self, name: str, persist: bool = False) -> dict:
        return await self._invoke("remove_server", {"name": name, "persist": persist})
