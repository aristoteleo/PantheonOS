"""Pantheon CLI - Entry point for python -m pantheon.cli"""

import asyncio
from .core import main, cli

if __name__ == "__main__":
    cli()