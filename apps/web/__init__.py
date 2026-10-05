import asyncio

from pantheon.utils.log import logger
from pantheon.toolset import ToolSet, tool


class WebToolSet(ToolSet):
    """Web toolset with fetch(using crawl4ai) and search capabilities using DDGS.

    Args:
        name: The name of the toolset.
        **kwargs: Additional keyword arguments.
    """

    def __init__(self, name: str, *, crawler_options=None, **kwargs):
        super().__init__(name, **kwargs)
        # Deployment-owned options, never tool-call arguments. The ordinary App
        # supplies its private cache directory; legacy callers keep their defaults.
        self._crawler_options = dict(crawler_options or {})

    @tool(job_type="thread")
    async def duckduckgo_search(
        self,
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

        def search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results, timelimit=time_limit))

        # DDGS is synchronous. The ordinary App shares its event loop with
        # health/lifecycle RPCs; preserve that loop while a search is in flight.
        pending = asyncio.create_task(asyncio.to_thread(search))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            # A cancelled caller must not make shutdown forget the live worker.
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not pending.cancelled():
                pending.exception()
            raise

    @tool(job_type="thread")
    async def web_crawl(
        self,
        urls: list[str],
        timeout: float = 20.0,
    ) -> list[str]:
        """
        Crawl the web and return the contents of the pages.
        Result will be in markdown format.

        Args:
            urls: List of URLs to crawl.
            timeout: Timeout for the web crawler.

        Returns:
            List of contents of the pages.
        """
        if isinstance(urls, str):  # a lone URL string would iterate char-by-char
            urls = [urls]
        from crawl4ai import AsyncWebCrawler

        async with AsyncWebCrawler(verbose=False, **self._crawler_options) as crawler:

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
