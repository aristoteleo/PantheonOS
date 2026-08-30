"""Compatibility shim — the MCP manager moved to pantheon.toolsets.mcp.

The gateway and manager are now part of the mcp-gateway App
(pantheon/toolsets/mcp/); the endpoint keeps working through this re-export
until pantheon/endpoint/ itself is removed.
"""

from pantheon.toolsets.mcp.manager import *  # noqa: F401,F403
from pantheon.toolsets.mcp.manager import (  # noqa: F401
    MCPManager,
    MCPPoolConfig,
    MCPServerConfig,
    MCPServerInstance,
    MCPServerType,
)
