"""End-to-end integration tests for Unified MCP Gateway.

These tests verify real server startup, connection, and tool discovery.
"""

import asyncio
import pytest
from pathlib import Path


class TestGatewayE2E:
    """End-to-end tests with real gateway startup and connections."""

    @pytest.fixture
    async def running_gateway(self):
        """Start a real gateway and yield it for tests."""
        from pantheon.endpoint.gateway import UnifiedMCPGateway
        
        gateway = UnifiedMCPGateway(port=3300)
        await gateway.start_gateway()
        
        yield gateway
        
        await gateway.stop_gateway()

    @pytest.mark.asyncio
    async def test_empty_gateway_accepts_connections(self, running_gateway):
        """Test that gateway with no mounted servers accepts MCP connections."""
        from fastmcp import Client
        
        async with Client(running_gateway.get_unified_uri()) as client:
            tools = await client.list_tools()
            
            assert isinstance(tools, list)
            assert len(tools) == 0

    @pytest.mark.asyncio
    async def test_gateway_with_mounted_server(self, running_gateway):
        """Test gateway with a mounted FastMCP server returns tools."""
        from fastmcp import FastMCP, Client
        
        # Create a test MCP server with a tool
        test_mcp = FastMCP("TestServer")
        
        @test_mcp.tool
        def echo(message: str) -> str:
            """Echo a message back."""
            return f"Echo: {message}"
        
        # Mount to gateway
        await running_gateway.mount_server("test", test_mcp)
        
        # Connect and list tools
        async with Client(running_gateway.get_unified_uri()) as client:
            tools = await client.list_tools()
            
            # Should have the echo tool with test_ prefix
            tool_names = [t.name for t in tools]
            assert "test_echo" in tool_names

    @pytest.mark.asyncio
    async def test_call_tool_through_gateway(self, running_gateway):
        """Test calling a tool through the unified gateway."""
        from fastmcp import FastMCP, Client
        
        test_mcp = FastMCP("CalcServer")
        
        @test_mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b
        
        await running_gateway.mount_server("calc", test_mcp)
        
        async with Client(running_gateway.get_unified_uri()) as client:
            result = await client.call_tool("calc_add", {"a": 5, "b": 3})
            
            # Extract result from structured content
            if hasattr(result, 'content') and result.content:
                text = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])
                assert "8" in text


class TestMCPManagerE2E:
    """End-to-end tests for MCPManager with real STDIO servers."""

    @pytest.fixture
    def echo_server_script(self, tmp_path):
        """Create a simple echo MCP server script."""
        script = tmp_path / "echo_server.py"
        script.write_text('''
from fastmcp import FastMCP

mcp = FastMCP("Echo")

@mcp.tool
def echo(message: str) -> str:
    """Echo a message."""
    return f"ECHO: {message}"

@mcp.tool
def reverse(text: str) -> str:
    """Reverse a string."""
    return text[::-1]

if __name__ == "__main__":
    mcp.run()
''')
        return str(script)

    @pytest.mark.asyncio
    async def test_stdio_server_mount_to_gateway(self, echo_server_script):
        """Test STDIO server starts and mounts to gateway correctly."""
        from pantheon.endpoint.mcp import MCPManager
        from fastmcp import Client
        
        manager = MCPManager(port=3310)
        
        # Load config with test server
        config = {
            "servers": {
                "echo": {
                    "type": "stdio",
                    "command": f"python {echo_server_script}",
                    "description": "Test echo server"
                }
            }
        }
        await manager.load_config(config)
        
        # Start server (this should also start gateway)
        result = await manager.start_services(["echo"])
        
        assert result["success"]
        assert "echo" in result["started"]
        
        # Wait a moment for server to fully initialize
        await asyncio.sleep(0.5)
        
        # Connect to unified gateway and verify tools
        try:
            async with Client(manager.get_unified_uri()) as client:
                tools = await client.list_tools()
                tool_names = [t.name for t in tools]
                
                # Should have prefixed tools
                assert "echo_echo" in tool_names
                assert "echo_reverse" in tool_names
        finally:
            # Cleanup
            await manager.stop_services(["echo"])
            await manager._gateway.stop_gateway()

    @pytest.mark.asyncio  
    async def test_mcp_provider_with_gateway(self, echo_server_script):
        """Test MCPProvider can connect to gateway and list tools."""
        from pantheon.endpoint.mcp import MCPManager
        from pantheon.providers import MCPProvider
        
        MCPProvider.clear_instances()
        
        manager = MCPManager(port=3320)
        
        config = {
            "servers": {
                "test": {
                    "type": "stdio", 
                    "command": f"python {echo_server_script}",
                }
            }
        }
        await manager.load_config(config)
        await manager.start_services(["test"])
        await asyncio.sleep(0.5)
        
        try:
            # Use MCPProvider (like real code does)
            provider = MCPProvider.get_instance(manager.get_unified_uri())
            tools = await provider.list_tools()
            
            assert len(tools) >= 2  # echo and reverse
            tool_names = [t.name for t in tools]
            assert "test_echo" in tool_names
        finally:
            await manager.stop_services(["test"])
            await manager._gateway.stop_gateway()


class TestPackageRuntimeE2E:
    """End-to-end tests for Package Runtime with MCP."""

    @pytest.fixture
    def sample_mcp_server(self, tmp_path):
        """Create a sample MCP server for Package Runtime testing."""
        script = tmp_path / "pkg_server.py"
        script.write_text('''
from fastmcp import FastMCP

mcp = FastMCP("PackageTest")

@mcp.tool
def pkg_method(x: int) -> int:
    """A package method."""
    return x * 2

if __name__ == "__main__":
    mcp.run()
''')
        return str(script)

    @pytest.mark.asyncio
    async def test_refresh_mcp_packages(self, sample_mcp_server, monkeypatch):
        """Test Package Runtime can discover MCP tools from gateway."""
        from pantheon.endpoint.mcp import MCPManager
        from pantheon.internal.package_runtime.manager import PackageManager
        from pantheon.providers import MCPProvider
        import tempfile
        
        MCPProvider.clear_instances()
        
        manager = MCPManager(port=3330)
        
        config = {
            "servers": {
                "pkgtest": {
                    "type": "stdio",
                    "command": f"python {sample_mcp_server}",
                }
            }
        }
        await manager.load_config(config)
        await manager.start_services(["pkgtest"])
        await asyncio.sleep(0.5)
        
        # Set env var for Package Runtime
        monkeypatch.setenv("ENDPOINT_MCP_URI", manager.get_unified_uri())
        
        try:
            # Create package manager and refresh
            with tempfile.TemporaryDirectory() as tmpdir:
                pkg_manager = PackageManager(tmpdir)
                result = await pkg_manager.refresh_mcp_packages()
                
                assert result["success"]
                # Should discover 'pkgtest' as a package
                assert "pkgtest" in result["packages"]
        finally:
            await manager.stop_services(["pkgtest"])
            await manager._gateway.stop_gateway()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
