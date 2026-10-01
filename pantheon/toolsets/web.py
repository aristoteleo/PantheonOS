import asyncio
import json
import os

from pantheon.utils.log import logger
from pantheon.toolset import ToolSet, tool

YOUCOM_FREE_MCP_URL = "https://api.you.com/mcp?profile=free"
YOUCOM_AUTHENTICATED_MCP_URL = "https://api.you.com/mcp"


def _parse_youcom_results(payload_text: str) -> list[dict]:
    """Normalize a You.com ``you-search`` response into DDG-style results.

    Mirrors the ``duckduckgo_search`` contract (``title``/``href``/``body``)
    so agents can consume either search tool without a format switch.
    """
    try:
        data = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"You.com search returned invalid JSON: {exc}") from exc

    payload = data.get("results") if isinstance(data, dict) else None
    web_results = payload.get("web") if isinstance(payload, dict) else None
    if not isinstance(web_results, list):
        raise RuntimeError("Unexpected You.com search response shape")

    results: list[dict] = []
    for item in web_results:
        if not isinstance(item, dict):
            continue
        body = str(item.get("description") or "")
        if not body:
            highlights = (item.get("contents") or {}).get("highlights") or []
            body = str(highlights[0]) if highlights else ""
        results.append(
            {
                "title": str(item.get("title") or ""),
                "href": str(item.get("url") or ""),
                "body": body,
            }
        )
    return results


class WebToolSet(ToolSet):
    """Web toolset with fetch(using crawl4ai) and search capabilities using DDGS or You.com.

    Args:
        name: The name of the toolset.
        **kwargs: Additional keyword arguments.
    """

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

        with DDGS() as ddgs:
            results = ddgs.text(
                query,
                max_results=max_results,
                timelimit=time_limit,
            )
        return list(results)

    @tool(job_type="thread")
    async def youcom_search(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict]:
        """Search the web for the query using You.com.

        Works without an API key through You.com's free MCP profile. Set
        the YDC_API_KEY environment variable to search through the
        authenticated endpoint instead. Results use the same shape as
        duckduckgo_search.

        Args:
            query: The query to search for.
            max_results: The maximum number of results to return.
        """
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        api_key = os.environ.get("YDC_API_KEY")
        if api_key:
            url = YOUCOM_AUTHENTICATED_MCP_URL
            headers = {"Authorization": f"Bearer {api_key}"}
        else:
            url = YOUCOM_FREE_MCP_URL
            headers = None
        transport = StreamableHttpTransport(url=url, headers=headers)

        async with Client(transport) as client:
            result = await client.call_tool(
                "you-search", {"query": query, "count": max_results}
            )

        payload_text = next(
            (block.text for block in result.content if hasattr(block, "text")),
            None,
        )
        if payload_text is None:
            raise RuntimeError("You.com search returned no text content")
        return _parse_youcom_results(payload_text)[:max_results]

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
