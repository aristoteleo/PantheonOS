"""PantheonOS: the platform's Python side.

The App SDK and hosting (appkit, apphost, toolset, remote), the General Team
the platform sets up for an owner (general_team), the local product's
owner (local), the Store client (store), model access (models,
model_contracts) and the infrastructure the platform and its Apps share
(utils, settings, funcdesc, package_runtime). Pantheon-Agent (the `pantheon`
package) is one App on top of it; nothing here imports it.
"""
import logging
import os
import warnings

# Suppress DeprecationWarnings from third-party packages, set before they load.
warnings.filterwarnings("ignore", category=DeprecationWarning)
os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning")

# MCP SDK INFO logs ("Processing request of type...") would pollute CLI output.
for _name in ("mcp", "mcp.server", "mcp.server.lowlevel.server"):
    logging.getLogger(_name).setLevel(logging.WARNING)

__version__ = "0.6.4"

# Modules that moved keep their old names (pantheonos/_moved.py).
from ._moved import install as _install_moved

_install_moved()
