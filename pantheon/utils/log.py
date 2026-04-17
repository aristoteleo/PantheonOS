import sys
import warnings
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

# Reconfigure stdout/stderr to use UTF-8 encoding (fixes Windows GBK issues with emoji)
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from loguru import logger as loguru_logger

LEVEL_MAP = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

# Track file handler ID for management
_file_handler_id: Optional[int] = None


@contextmanager
def temporary_log_level(level: str):
    """Context manager to temporarily set log level for loguru logger

    Usage:
        with temporary_log_level("WARNING"):
            agent.run()  # Only WARNING and ERROR will be logged
    """
    # Use loguru's contextualize to set a context variable
    # Then the filter checks this variable to decide whether to log
    with loguru_logger.contextualize(log_level_override=level):
        yield


# Configure loguru handler with context-aware filter
def _context_aware_filter(record):
    """Filter that respects context-local log level settings"""
    override_level = record["extra"].get("log_level_override")
    if override_level is None:
        return True  # No override, allow all logs

    override_level_num = LEVEL_MAP.get(override_level, 0)
    record_level_num = record["level"].no
    return record_level_num >= override_level_num


logger = loguru_logger

# Track if logging has been explicitly disabled
_logging_disabled = False

# Apply context-aware filter to all handlers
# Remove default handler and add new one with our filter
# Use stdout instead of stderr so it works with prompt_toolkit's patch_stdout
# Track console handler ID for management
_console_handler_id: Optional[int] = None

# Apply context-aware filter to all handlers
# Remove default handler and add new one with our filter
loguru_logger.remove()
_console_handler_id = loguru_logger.add(sys.stdout, filter=_context_aware_filter, level="WARNING")


def set_level(level: str):
    """Set the logging level for the console handler.

    This safely replaces only the console handler, preserving other handlers
    (like file handlers).
    """
    global _logging_disabled, _console_handler_id
    if _logging_disabled:
        return  # Don't re-enable if disabled

    # Remove existing console handler if we have its ID
    if _console_handler_id is not None:
        try:
            loguru_logger.remove(_console_handler_id)
        except ValueError:
            pass  # Handler might have been removed elsewhere

    # Add new console handler
    _console_handler_id = loguru_logger.add(sys.stdout, filter=_context_aware_filter, level=level)


def disable_all():
    """Completely disable all logging. Cannot be re-enabled."""
    global _logging_disabled
    _logging_disabled = True
    loguru_logger.remove()
    loguru_logger.disable("pantheon")


def setup_file_logging(
    log_dir: Optional[Path] = None,
    level: str = "INFO",
    session_name: str = "repl",
) -> Path:
    """Setup file logging to save logs to a file.
    
    This is useful in REPL mode where console logging is suppressed,
    but you still want to capture logs for debugging.
    
    The file log level defaults to INFO, which captures INFO, WARNING, and ERROR
    logs while avoiding verbose DEBUG output. This provides a good balance
    between having useful diagnostic information and avoiding excessive log size.
    
    Args:
        log_dir: Directory for log files. Defaults to settings.logs_dir (.pantheon/logs)
        level: Log level for file handler (default: INFO - captures most useful logs)
        session_name: Prefix for log file name (default: "repl")
        
    Returns:
        Path to the created log file
    """
    global _file_handler_id
    
    # Import settings lazily to avoid circular imports
    if log_dir is None:
        from pantheon.settings import get_settings
        log_dir = get_settings().logs_dir
    
    # Ensure log directory exists
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate timestamped log file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{session_name}_{timestamp}.log"
    
    # Remove existing file handler if any
    if _file_handler_id is not None:
        try:
            loguru_logger.remove(_file_handler_id)
        except ValueError:
            pass  # Handler already removed
    
    # Add new file handler - captures all logs regardless of console level
    _file_handler_id = loguru_logger.add(
        log_file,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )
    
    return log_file


# =============================================================================
# Warning Suppression
# =============================================================================

# Suppress aiohttp "Unclosed client session" warnings.
# These warnings are harmless - the OS cleans up connections on process exit.
warnings.filterwarnings("ignore", message="Unclosed client session", category=ResourceWarning)
warnings.filterwarnings("ignore", message="Unclosed connector", category=ResourceWarning)

# Suppress websockets deprecation warnings from uvicorn
warnings.filterwarnings("ignore", message="websockets.legacy is deprecated", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="websockets.server.WebSocketServerProtocol is deprecated", category=DeprecationWarning)


def suppress_aiohttp_warnings(loop, context) -> None:
    """Custom asyncio exception handler to suppress aiohttp cleanup warnings.
    
    aiohttp prints warnings via asyncio's exception handler during GC.
    Use with: loop.set_exception_handler(suppress_aiohttp_warnings)
    """
    message = context.get("message", "")
    if "Unclosed" in message:
        return  # Suppress aiohttp cleanup warnings
    # For other exceptions, use default handling
    loop.default_exception_handler(context)
