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

# Suppress litellm debug output via env vars (avoid importing litellm at startup,
# it costs ~1.5s. The actual suppress_debug_info/set_verbose flags are set in
# utils/llm.py:import_litellm() the first time litellm is actually used.)
os.environ.setdefault("LITELLM_LOG", "ERROR")
# Suppress CLIENT_IP_ENCRYPTION_KEY warning by setting a default value
os.environ.setdefault("CLIENT_IP_ENCRYPTION_KEY", "pantheon-default-key")

# Suppress MCP SDK INFO logs ("Processing request of type...") that pollute CLI output
import logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("mcp.server").setLevel(logging.WARNING)
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

__version__ = "0.5.1"
