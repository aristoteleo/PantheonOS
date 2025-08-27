"""Web Toolset - Complete web functionality using DDGS"""

import re
import ssl
import urllib.request
from typing import Dict, Any, List
from ..utils.log import logger

# Core libraries
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

from ..toolset import ToolSet, tool


class WebToolSet(ToolSet):
    """Web toolset with fetch(using urllib) and search capabilities using DDGS"""

    def __init__(
        self,
        name: str,
        worker_params: dict | None = None,
        **kwargs,
    ):
        """
        Initialize the Web Toolset.
        
        Args:
            name: Name of the toolset
            worker_params: Parameters for the worker
            **kwargs: Additional keyword arguments
        """
        super().__init__(name, worker_params, **kwargs)
        
        # Disable SSL warnings globally
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # Setup requests session if available
        if REQUESTS_AVAILABLE:
            self.session = requests.Session()
            # Setup retry strategy
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)
            
            # Setup realistic headers
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'no-cache'
            })
        
        # Always set up SSL context for urllib (used in web_search)
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # Cache for web content to avoid repeated requests
        self._content_cache = {}

    def _clean_html_to_markdown(self, html_content: str, base_url: str = "") -> str:
        """Convert HTML to markdown-like text using BeautifulSoup or regex fallback"""
        if BS4_AVAILABLE:
            try:
                # Use BeautifulSoup for better HTML parsing
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Remove unwanted elements
                for element in soup(["script", "style", "meta", "link", "noscript", "nav", "footer", "aside"]):
                    element.decompose()
                
                # Convert links first (before other elements)
                links_found = []
                for link in soup.find_all('a', href=True):
                    text = link.get_text().strip()
                    href = link['href']
                    
                    # Convert relative URLs to absolute
                    if href.startswith('/') and base_url:
                        from urllib.parse import urljoin
                        href = urljoin(base_url, href)
                    elif href.startswith('#'):
                        continue  # Skip anchor links
                    elif not href.startswith('http') and not href.startswith('mailto:') and base_url:
                        # Handle relative paths for documentation sites
                        from urllib.parse import urljoin
                        if 'readthedocs' in base_url:
                            # Special handling for ReadTheDocs
                            if '/en/latest/' in base_url or base_url.endswith('.io/'):
                                if base_url.endswith('.io/'):
                                    href = urljoin(base_url, f'en/latest/{href}/')
                                else:
                                    href = urljoin(base_url, f'{href}/')
                        else:
                            href = urljoin(base_url, href)
                    
                    if text and len(text) > 0:
                        link.replace_with(f"[{text}]({href})")
                        if href.startswith('http'):
                            links_found.append(f"- {text}: {href}")
                    else:
                        link.replace_with(f"({href})")
                
                # Convert headings
                for i in range(1, 7):
                    for heading in soup.find_all(f'h{i}'):
                        text = heading.get_text().strip()
                        if text:
                            heading.replace_with(f"\n{'#' * i} {text}\n")
                
                # Convert lists
                for ul in soup.find_all('ul'):
                    items = ul.find_all('li')
                    list_text = "\n"
                    for li in items:
                        item_text = li.get_text().strip()
                        if item_text:
                            list_text += f"- {item_text}\n"
                    ul.replace_with(list_text)
                
                # Convert paragraphs
                for p in soup.find_all('p'):
                    text = p.get_text().strip()
                    if text:
                        p.replace_with(f"\n{text}\n")
                
                # Get cleaned text
                text = soup.get_text()
                
                # Clean up whitespace
                text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
                text = re.sub(r' +', ' ', text)
                text = text.strip()
                
                # Add links section at the end if we found many links
                if len(links_found) > 3:
                    text += "\n\n## Links Found:\n" + "\n".join(links_found[:10])
                
                return text
                
            except Exception as e:
                pass
        
        # Fallback to regex-based cleaning
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Convert headings
        for i in range(1, 7):
            html_content = re.sub(f'<h{i}[^>]*>(.*?)</h{i}>', f'\\n{"#" * i} \\\\1\\n', html_content, flags=re.IGNORECASE)
        
        # Convert links
        html_content = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\\2](\\1)', html_content, flags=re.IGNORECASE)
        
        # Convert paragraphs
        html_content = re.sub(r'<p[^>]*>(.*?)</p>', r'\\n\\1\\n', html_content, flags=re.IGNORECASE)
        
        # Remove remaining HTML tags
        html_content = re.sub(r'<[^>]+>', ' ', html_content)
        
        # Clean up whitespace
        html_content = re.sub(r'\\n\\s*\\n\\s*\\n', '\\n\\n', html_content)
        html_content = re.sub(r' +', ' ', html_content)
        html_content = html_content.strip()
        
        return html_content
    
    def _is_binary_or_corrupted(self, content: str) -> bool:
        """Check if content appears to be binary or corrupted"""
        if not content:
            return False
        
        # Check for high ratio of non-printable characters
        printable_chars = sum(1 for c in content[:1000] if c.isprintable() or c.isspace())
        total_chars = min(len(content), 1000)
        
        if total_chars == 0:
            return False
        
        printable_ratio = printable_chars / total_chars
        return printable_ratio < 0.7  # If less than 70% printable, consider it binary

    def _fetch_with_requests(self, url: str, timeout: int = 30) -> Dict[str, Any]:
        """Fetch using requests library with better handling"""
        try:
            response = self.session.get(url, timeout=timeout, verify=False, stream=True)
            response.raise_for_status()
            
            # Get content with encoding detection
            content = response.text
            
            return {
                "success": True,
                "content": content,
                "status_code": response.status_code,
                "content_type": response.headers.get('Content-Type', '').lower(),
                "method": "requests"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Requests error: {str(e)}",
                "method": "requests"
            }

    @tool
    async def web_fetch(self, url: str) -> Dict[str, Any]:
        """
        Fetch and display content from a web URL.
        
        Args:
            url: The URL to fetch content from
            
        Returns:
            dict with the fetched content and metadata
        """
        # Display the URL being fetched
        logger.info("")
        logger.info(f"╭─ [cyan]Fetching:[/cyan] {url}")
        logger.info("╰" + "─" * min(len(url) + 12, 74))
        
        # Check cache first
        cache_key = f"fetch_{url}"
        if cache_key in self._content_cache:
            cached_result = self._content_cache[cache_key]
            logger.info("[dim]💾 Using cached result[/dim]")
            return cached_result
        
        try:
            # Use requests if available, otherwise urllib
            if REQUESTS_AVAILABLE:
                result = self._fetch_with_requests(url, timeout=30)
            else:
                # Fallback to urllib
                request = urllib.request.Request(
                    url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                    }
                )
                
                try:
                    with urllib.request.urlopen(request, timeout=30, context=self.ssl_context) as response:
                        content = response.read().decode('utf-8', errors='ignore')
                        result = {
                            "success": True,
                            "content": content,
                            "status_code": response.getcode(),
                            "content_type": response.getheader('Content-Type', '').lower(),
                            "method": "urllib"
                        }
                except Exception as e:
                    result = {
                        "success": False,
                        "error": f"Urllib error: {str(e)}",
                        "method": "urllib"
                    }
            
            if not result["success"]:
                logger.info(f"[red]❌ Failed to fetch: {result['error']}[/red]")
                return {
                    "success": False,
                    "error": result["error"],
                    "content": "",
                    "url": url,
                    "method": result.get("method", "unknown")
                }
            
            content = result["content"]
            logger.info(f"[green]✅ Successfully fetched {len(content)} characters[/green]")
            
            # Check if content is binary or corrupted
            if self._is_binary_or_corrupted(content):
                logger.info("[yellow]⚠️ Content appears to be binary or corrupted[/yellow]")
                return {
                    "success": False,
                    "error": "Content appears to be binary or corrupted",
                    "content": "",
                    "url": url,
                    "method": result["method"]
                }
            
            # Convert HTML to markdown-like format
            if 'html' in result.get("content_type", ""):
                logger.info("[dim]Converting HTML to markdown...[/dim]")
                content = self._clean_html_to_markdown(content, url)
            
            # Cache the result
            fetch_result = {
                "success": True,
                "content": content,
                "url": url,
                "status_code": result.get("status_code", 200),
                "content_type": result.get("content_type", ""),
                "method": result["method"]
            }
            
            self._content_cache[cache_key] = fetch_result
            
            return fetch_result
            
        except Exception as e:
            logger.info(f"[red]❌ Unexpected error: {str(e)}[/red]")
            return {
                "success": False,
                "error": f"Web fetch error: {str(e)}",
                "content": "",
                "url": url,
                "method": "error"
            }

    def _print_search_results(self, query: str, results: List[Dict[str, Any]]):
        """Print search results in a nice format"""
        title = f"🔍 WebSearch: {query}"
        logger.info("╭─ [bold cyan]" + title + "[/bold cyan] " + "─" * (74 - len(title) - 4) + "╮")
        
        if not results:
            logger.info("│ [yellow]No search results found[/yellow]" + " " * 35 + "│")
        else:
            for i, result in enumerate(results, 1):
                title = result.get('title', 'No title')[:60]
                snippet = result.get('snippet', '')[:60] 
                url = result.get('url', '')[:70]
                
                logger.info(f"│ [bold]{i}.[/bold] [green]{title}[/green]" + " " * max(0, 60 - len(title)) + "│")
                if snippet:
                    lines = [snippet[i:i+64] for i in range(0, len(snippet), 64)]
                    for line in lines[:2]:  # Max 2 lines
                        logger.info(f"│   [dim]{line}[/dim]" + " " * max(0, 68 - len(line)) + "│")
                logger.info(f"│   [cyan]{url}[/cyan]" + " " * max(0, 68 - len(url)) + "│")
                if i < len(results):
                    logger.info("│" + " " * 72 + "│")
                    
        logger.info("╰" + "─" * 74 + "╯")

    async def _search_with_ddgs(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """Search using the DDGS library (most reliable)"""
        try:
            if not DDGS_AVAILABLE:
                return {"success": False, "error": "DDGS library not available", "results": []}
            
            # Use the DDGS library for search
            with DDGS() as ddgs:
                search_results = list(ddgs.text(query, max_results=max_results))
                
                results = []
                for result in search_results:
                    results.append({
                        'title': result.get('title', ''),
                        'url': result.get('href', ''),
                        'snippet': result.get('body', ''),
                        'source': 'DuckDuckGo API',
                        'engine': 'ddgs'
                    })
                
                return {
                    "success": True,
                    "results": results,
                    "method": "ddgs"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"DDGS search error: {str(e)}",
                "method": "ddgs"
            }

    @tool 
    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        region: str = "us-en",
        engine: str = "ddgs"
    ) -> Dict[str, Any]:
        """
        Search the web using DuckDuckGo search (DDGS library).
        
        Args:
            query: Search query string
            max_results: Maximum number of results to return (default: 5)
            region: Region for search results (default: us-en)  
            engine: Search engine preference (only "ddgs" supported)
            
        Returns:
            dict with search results
        """
        # Use DDGS as the primary and only search method
        if not DDGS_AVAILABLE:
            logger.info("[red]❌ DDGS library not available[/red]")
            self._print_search_results(query, [])
            return {
                "success": False,
                "error": "DDGS library not available. Please install with: pip install ddgs",
                "query": query,
                "results": [],
                "total_results": 0
            }
        
        try:
            result = await self._search_with_ddgs(query, max_results)
            
            if result["success"] and result["results"]:
                results = result["results"]
                logger.info(f"[green]✅ Found {len(results)} search results[/green]")
                self._print_search_results(query, results)
                return {
                    "success": True,
                    "query": query,
                    "results": results,
                    "total_results": len(results),
                    "method": "DuckDuckGo API"
                }
        except Exception as e:
            logger.info(f"[red]❌ Search error: {str(e)}[/red]")
            
        # If DDGS fails, return error
        logger.info("[yellow]⚠️ Search failed[/yellow]")
        self._print_search_results(query, [])
        return {
            "success": False,
            "error": "Search failed",
            "query": query,
            "results": [],
            "total_results": 0,
            "method": "ddgs"
        }


__all__ = ["WebToolSet"]
