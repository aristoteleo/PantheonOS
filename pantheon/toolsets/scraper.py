import os
import httpx
import asyncio
import traceback
from typing import Dict, Any, List

from ..toolset import ToolSet, tool
from ..utils.log import logger


class ScraperToolSet(ToolSet):
    """Toolset for using ScraperAPI to perform google search and web crawl.
    If you want to use this toolset, you need to set the environment variable SCRAPER_API_KEY.
    """

    @tool(job_type="thread")
    async def google_search(
        self,
        query: str,
        max_results: int = 10,
        country: str = "us",
        language: str = "en",
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Search using ScraperAPI Google search.

        Args:
            query: The search query.
            max_results: Maximum number of results to return (default: 10).
            country: Country code for search (default: us).
            language: Language code for search (default: en).
            timeout: Timeout for the request in seconds (default: 30.0).

        Returns:
            Search results from ScraperAPI.
        """
        api_key = os.getenv("SCRAPER_API_KEY")
        if not api_key:
            raise ValueError("SCRAPER_API_KEY environment variable is not set")

        url = "https://api.scraperapi.com/structured/google/search"
        
        payload = {
            "api_key": api_key,
            "query": query,
            "num": max_results,
            "country": country,
            "language": language
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=payload, timeout=timeout)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP error occurred while searching: {e}")
            raise
        except Exception as e:
            logger.error(f"Error occurred while searching: {e}")
            raise

    @tool(job_type="thread")
    async def fetch_web_page(
        self,
        urls: List[str],
        output_format: str = "markdown",
        timeout: float = 30.0
    ) -> List[str]:
        """Fetch web pages using ScraperAPI.

        Args:
            urls: List of URLs to fetch.
            output_format: Output format (markdown, html, text) (default: markdown).
            timeout: Timeout for each request in seconds (default: 30.0).

        Returns:
            List of contents from the web pages.
        """
        api_key = os.getenv("SCRAPER_API_KEY")
        if not api_key:
            raise ValueError("SCRAPER_API_KEY environment variable is not set")

        async def fetch_single_url(url: str) -> str:
            """Fetch a single URL using ScraperAPI."""
            scraper_url = "https://api.scraperapi.com/"
            
            payload = {
                "api_key": api_key,
                "url": url,
                "output_format": output_format
            }

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(scraper_url, params=payload, timeout=timeout)
                    response.raise_for_status()
                    return response.text
            except httpx.HTTPError as e:
                logger.error(f"HTTP error occurred while fetching {url}: {e}")
                traceback.print_exc()
                return ""
            except Exception as e:
                logger.error(f"Error occurred while fetching {url}: {e}")
                traceback.print_exc()
                return ""

        # Create tasks for all URLs
        tasks = [fetch_single_url(url) for url in urls]
        
        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results, handling any exceptions
        contents = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch {urls[i]}: {result}")
                contents.append("")
            else:
                contents.append(result)
        
        return contents


__all__ = ["ScraperToolSet"]
