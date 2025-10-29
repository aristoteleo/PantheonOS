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


logger = loguru_logger


def use_rich_mode():
    global logger
    logger = RichLogger()


def set_level(level: str):
    if isinstance(logger, RichLogger):
        logger.set_level(level)
    else:
        loguru_logger.remove()
        loguru_logger.add(sys.stdout, level=level)


def disable(name: str):
    loguru_logger.disable(name)
