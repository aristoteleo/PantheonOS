import asyncio

from ..utils.log import logger
from ..toolset import ToolSet, tool


class WebToolSet(ToolSet):
    """Web toolset with fetch(using crawl4ai) and search capabilities using DDGS.

    Args:
        name: The name of the toolset.
        worker_params: The parameters for the worker.
        **kwargs: Additional keyword arguments.
    """

    @tool(job_type="thread")
    async def duckduckgo_search(
            query: str,
            max_results: int = 10,
            time_limit: str | None = None,
            ):
        """Search the web for the query.
    
        Args:
            query: The query to search for.
            max_results: The maximum number of results to return.
            time_limit: The time limit for the search. d, w, m, y.
                Defaults to None.
        """
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = ddgs.text(
                query,
                max_results=max_results,
                timelimit=time_limit,
            )
        return results

    @tool(job_type="thread")
    async def web_crawl(urls: list[str], timeout: float = 20.0) -> list[str]:
        """
        Crawl the web and return the contents of the pages.
        Result will be in markdown format.
    
        Args:
            urls: List of URLs to crawl.
            timeout: Timeout for the web crawler.
    
        Returns:
            List of contents of the pages.
        """
        from crawl4ai import AsyncWebCrawler
        async with AsyncWebCrawler(verbose=False) as crawler:
            async def run_crawler(url):
                try:
                    res = await asyncio.wait_for(crawler.arun(url=url), timeout=timeout)
                    return res
                except asyncio.TimeoutError:
                    return None
            tasks = [run_crawler(url) for url in urls]
            results = await asyncio.gather(*tasks)
        contents = []
        for result in results:
            try:
                contents.append(result.markdown.raw_markdown)
            except Exception as e:
                logger.error(e)
                contents.append("")
        return contents


__all__ = ["WebToolSet"]
