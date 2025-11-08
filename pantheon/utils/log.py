import sys
from contextlib import contextmanager
from loguru import logger as loguru_logger
from rich.console import Console

console = Console()

LEVEL_MAP = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}


class RichLogger:
    def __init__(self):
        self.level = LEVEL_MAP["INFO"]

    def set_level(self, level: str):
        self.level = LEVEL_MAP[level]

    def info(self, message: str):
        if self.level > LEVEL_MAP["INFO"]:
            return
        console.print(message)

    def error(self, message: str):
        if self.level > LEVEL_MAP["ERROR"]:
            return
        console.print(message)

    def warning(self, message: str):
        if self.level > LEVEL_MAP["WARNING"]:
            return
        console.print(message)

    def debug(self, message: str):
        if self.level > LEVEL_MAP["DEBUG"]:
            return
        console.print(message)


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

# Apply context-aware filter to all handlers
# Remove default handler and add new one with our filter
loguru_logger.remove()
loguru_logger.add(sys.stderr, filter=_context_aware_filter, level="DEBUG")


def use_rich_mode():
    global logger
    logger = RichLogger()


def set_level(level: str):
    if isinstance(logger, RichLogger):
        logger.set_level(level)
    else:
        loguru_logger.remove()
        loguru_logger.add(sys.stderr, filter=_context_aware_filter, level=level)


def disable(name: str):
    loguru_logger.disable(name)
