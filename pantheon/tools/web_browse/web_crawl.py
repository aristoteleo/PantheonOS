from crawl4ai import AsyncWebCrawler
import asyncio

from ...utils.log import logger


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
