import os
from pathlib import Path

# Jupyter path migration: use platformdirs standard (future-proof for jupyter_core v6)
os.environ.setdefault("JUPYTER_PLATFORM_DIRS", "1")

# Project root: captured at module load time, before any chdir
# This is the directory from which the process was started
# Used by config system, TemplateManager, etc. to find .pantheon/ directory
PROJECT_ROOT = Path.cwd().resolve()

# User-level Pantheon directory (global config, not project-specific)
PANTHEON_DIR = os.path.realpath(
    os.path.expanduser(os.environ.get("CONFIG_DIR", "~/.pantheon"))
)
CONFIG_FILE = os.path.join(PANTHEON_DIR, "config.yaml")
CLI_HISTORY_FILE = os.path.join(PANTHEON_DIR, "cli_history")

# Directories to ignore for file path completion (hidden files like .env are kept)
FILE_COMPLETION_IGNORED = {
    # Version control
    ".git", ".svn", ".hg",
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    # Node.js
    "node_modules",
    # Build output
    "dist", "build",
    # Pantheon
    ".pantheon", ".endpoint-logs", ".executor",
}
