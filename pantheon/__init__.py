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

# Suppress litellm debug output - must import and configure immediately
os.environ.setdefault("LITELLM_LOG", "ERROR")
# Suppress CLIENT_IP_ENCRYPTION_KEY warning by setting a default value
os.environ.setdefault("CLIENT_IP_ENCRYPTION_KEY", "pantheon-default-key")
import litellm
litellm.suppress_debug_info = True
litellm.set_verbose = False

__version__ = "0.4.2"
