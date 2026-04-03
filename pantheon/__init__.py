import warnings
import os

# Suppress ALL DeprecationWarnings from third-party packages
# This is set very early before any other imports to catch websockets/uvicorn/pydantic warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Also set env var for any subprocesses
os.environ["PYTHONWARNINGS"] = "ignore::DeprecationWarning"

# Pre-import problematic modules while our filter is active
# This ensures the deprecation warnings are suppressed during their import
try:
    import websockets.legacy
    import uvicorn.protocols.websockets.websockets_impl
except ImportError:
    pass  # OK if not installed

try:
    import fastapi  # noqa: F401 — suppress HTTP_422 DeprecationWarning at import time
except ImportError:
    pass


# Suppress MCP SDK INFO logs ("Processing request of type...") that pollute CLI output
import logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("mcp.server").setLevel(logging.WARNING)
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

__version__ = "0.5.1"
