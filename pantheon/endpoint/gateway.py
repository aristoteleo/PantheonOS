"""Compatibility shim — the unified gateway moved to pantheon.toolsets.mcp.

Part of the mcp-gateway App now (pantheon/toolsets/mcp/); this re-export
keeps the endpoint import path alive until pantheon/endpoint/ is removed.
"""

from pantheon.toolsets.mcp.gateway import (  # noqa: F401
    MountedServerInfo,
    UnifiedMCPGateway,
)
