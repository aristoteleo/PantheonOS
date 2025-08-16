"""Pantheon CLI Core - Main entry point for the CLI assistant (Refactored)"""

from rich.console import Console

# Import management modules
from .manager.api_key_manager import APIKeyManager
from .manager.model_manager import ModelManager

from ..utils.log import logger


async def main(yaml_config: str, log_level: str = "INFO"):
    """
    Start the Pantheon CLI assistant.
    
    Args:
        yaml_config: Path to YAML config file
    """
    raise NotImplementedError("Pantheon CLI is not implemented yet")
    console = Console()
    def custom_sink(message):
        console.print(message, end="")

    logger.configure(
        handlers=[
            {"sink":custom_sink, "format":"{message}", "level": log_level},
        ]
    )
    logger.disable("executor.engine")

    
    await agent.chat()
