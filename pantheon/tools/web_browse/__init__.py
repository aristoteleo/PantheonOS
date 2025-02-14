from functools import wraps

from .duckduckgo import duckduckgo_search
from .web_crawl import web_crawl

from ...remote.toolset import ToolSet, tool


class WebBrowseToolSet(ToolSet):
    @tool
    @wraps(duckduckgo_search)
    async def duckduckgo_search(
            self,
            query: str,
            max_results: int = 10,
            time_limit: str | None = None,
        ):
        return duckduckgo_search(query, max_results, time_limit)

    @tool
    @wraps(web_crawl)
    async def web_crawl(
            self,
            urls: list[str],
            timeout: float = 20.0,
        ):
        return web_crawl(urls, timeout)
