import sys
from loguru import logger as loguru_logger
from rich.console import Console

console = Console()

LEVEL_MAP = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}

class CustomLogger:
    def __init__(self, rich_mode: bool = False):
        self.rich_mode = rich_mode
        self.level = LEVEL_MAP["INFO"]

    def use_rich_mode(self):
        self.rich_mode = True

    def set_level(self, level: str):
        self.level = LEVEL_MAP[level]
        if not self.rich_mode:
            loguru_logger.remove()
            loguru_logger.add(sys.stdout, level=self.level)

    def disable(self, name: str):
        loguru_logger.disable(name)

    def info(self, message: str, rich = None):
        if self.level > LEVEL_MAP["INFO"]:
            return
        if self.rich_mode:
            console.print(message)
        else:
            loguru_logger.info(message)
        if rich is not None:
            console.print(rich)

    def error(self, message: str, rich = None):
        if self.level > LEVEL_MAP["ERROR"]:
            return
        if self.rich_mode:
            console.print(message)
        else:
            loguru_logger.error(message)
        if rich is not None:
            console.print(rich)

    def warning(self, message: str, rich = None):
        if self.level > LEVEL_MAP["WARNING"]:
            return
        if self.rich_mode:
            console.print(message)
        else:
            loguru_logger.warning(message)
        if rich is not None:
            console.print(rich)

    def debug(self, message: str, rich = None):
        if self.level > LEVEL_MAP["DEBUG"]:
            return
        if self.rich_mode:
            console.print(message)
        else:
            loguru_logger.debug(message)
        if rich is not None:
            console.print(rich)


logger = CustomLogger()


__all__ = ["logger"]
