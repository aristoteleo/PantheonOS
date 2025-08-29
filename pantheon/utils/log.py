import sys
from loguru import logger
from rich.console import Console

console = Console()

LEVEL_MAP = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}

class CustomLogger:
    def __init__(self, use_rich: bool = True):
        self.use_rich = use_rich
        self.level = LEVEL_MAP["INFO"]

    def set_level(self, level: str):
        self.level = LEVEL_MAP[level]
        if not self.use_rich:
            logger.remove()
            logger.add(sys.stdout, level=self.level)

    def disable(self, name: str):
        logger.disable(name)

    def info(self, message: str, rich = None):
        if self.level > LEVEL_MAP["INFO"]:
            return
        if self.use_rich:
            console.print(message)
        else:
            logger.info(message)
        if rich is not None:
            console.print(rich)

    def error(self, message: str, rich = None):
        if self.level > LEVEL_MAP["ERROR"]:
            return
        if self.use_rich:
            console.print(message)
        else:
            logger.error(message)
        if rich is not None:
            console.print(rich)

    def warning(self, message: str, rich = None):
        if self.level > LEVEL_MAP["WARNING"]:
            return
        if self.use_rich:
            console.print(message)
        else:
            logger.warning(message)
        if rich is not None:
            console.print(rich)

    def debug(self, message: str, rich = None):
        if self.level > LEVEL_MAP["DEBUG"]:
            return
        if self.use_rich:
            console.print(message)
        else:
            logger.debug(message)
        if rich is not None:
            console.print(rich)


logger = CustomLogger()


__all__ = ["logger"]
