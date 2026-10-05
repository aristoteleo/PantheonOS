"""Web search and browser-based crawling as an ordinary shared App."""
import os
from pathlib import Path

from pantheon.apps.toolset_backend import register_toolset
from . import WebToolSet

METHODS = frozenset(('duckduckgo_search', 'web_crawl'))


class ManagedWeb(WebToolSet):
    async def cleanup(self):
        from crawl4ai.async_database import async_db_manager
        await async_db_manager.cleanup()
        await super().cleanup()


def create_service(state_directory):
    directory = Path(state_directory)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('Web needs an existing absolute App state directory')
    # Crawl4AI's database directory is captured at import time, independently
    # of AsyncWebCrawler(base_directory). This process belongs to one Web App.
    os.environ['CRAWL4_AI_BASE_DIRECTORY'] = str(directory)
    from crawl4ai import BrowserConfig
    from crawl4ai.async_database import async_db_manager
    if Path(async_db_manager.db_path).parent.resolve() != (directory / '.crawl4ai').resolve():
        raise RuntimeError('Crawl4AI was initialized for another App; start Web in its own process')
    service = ManagedWeb('web', crawler_options={
        'base_directory': str(directory),
        'config': BrowserConfig(headless=True, ignore_https_errors=False),
    })
    service.functions = {name: value for name, value in service.functions.items() if name in METHODS}
    if service.functions.keys() != METHODS:
        raise ValueError('Web package methods differ from the source toolset')
    return service


async def register(ctx):
    await register_toolset(ctx, create_service(ctx.state_dir))
