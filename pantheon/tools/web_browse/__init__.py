from .duckduckgo import duckduckgo_search
from .web_crawl import web_crawl

from ...remote.toolset import ToolSet, tool


class WebBrowseToolSet(ToolSet):
    @tool(job_type="thread")
    async def duckduckgo_search(
            self,
            query: str,
            max_results: int = 10,
            time_limit: str | None = None,
        ):
        return duckduckgo_search(query, max_results, time_limit)

    @tool(job_type="thread")
    async def web_crawl(
            self,
            urls: list[str],
            timeout: float = 20.0,
        ):
        res = await web_crawl(urls, timeout)
        return res
