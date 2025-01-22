from crawl4ai import AsyncWebCrawler
import asyncio


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
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            tasks = [crawler.arun(url=url) for url in urls]
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
    except asyncio.TimeoutError:
        return []
    contents = []
    for result in results:
        try:
            contents.append(result.markdown_v2.raw_markdown)
        except Exception as e:
            print(e)
            contents.append("")
    return contents
