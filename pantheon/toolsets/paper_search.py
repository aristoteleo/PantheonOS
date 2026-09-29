"""Scholarly search: Semantic Scholar's graph API, then OpenAlex, then arXiv's export API.

None needs a key. Semantic Scholar without a key shares a low public rate limit and often answers
429; OpenAlex (open, broad, abstracts as an inverted index) then answers the same query; arXiv is
the last resort (measured 2026-09-24: its front end answers Python HTTP clients 406 on cache
misses while curl gets 200, so from Python it mostly fails). Results are titles, years, venues,
URLs and abstracts, which is what a literature scan needs before deciding which pages to fetch.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


def _clip(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _get(url: str, timeout: float, params: dict | None = None, tries: int = 3):
    """GET with a short backoff on throttling. Both APIs throttle anonymous callers: Semantic
    Scholar with 429, arXiv's front cache with 406 (measured 2026-09-24: the same query answered
    406 on a cache miss and 200 a few seconds later) or 503."""
    import time

    import requests

    for i in range(tries):
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code not in (406, 429, 503) or i == tries - 1:
            r.raise_for_status()
            return r
        time.sleep(3.0 * (i + 1))
    raise RuntimeError("unreachable")


def search_semantic_scholar(query: str, max_results: int, abstract_chars: int,
                            timeout: float = 15.0) -> List[Dict[str, Any]]:
    r = _get(S2_URL, timeout, {"query": query, "limit": max_results,
                               "fields": "title,year,venue,url,abstract,citationCount,externalIds"},
             tries=2)
    out = []
    for p in (r.json() or {}).get("data") or []:
        ext = p.get("externalIds") or {}
        url = p.get("url") or (f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv") else "")
        out.append({"title": p.get("title") or "", "year": p.get("year"), "venue": p.get("venue") or "",
                    "citations": p.get("citationCount"), "url": url,
                    "abstract": _clip(p.get("abstract"), abstract_chars)})
    return out


def search_openalex(query: str, max_results: int, abstract_chars: int,
                    timeout: float = 15.0) -> List[Dict[str, Any]]:
    import os

    params = {"search": query, "per-page": max_results,
              "select": "id,title,publication_year,primary_location,doi,cited_by_count,"
                        "abstract_inverted_index"}
    if os.environ.get("PANTHEON_CONTACT_EMAIL"):          # OpenAlex's "polite pool"
        params["mailto"] = os.environ["PANTHEON_CONTACT_EMAIL"]
    r = _get(OPENALEX_URL, timeout, params)
    out = []
    for w in (r.json() or {}).get("results") or []:
        inv = w.get("abstract_inverted_index") or {}
        words = sorted((p, t) for t, ps in inv.items() for p in ps)
        loc = w.get("primary_location") or {}
        out.append({"title": w.get("title") or "", "year": w.get("publication_year"),
                    "venue": (loc.get("source") or {}).get("display_name") or "",
                    "citations": w.get("cited_by_count"),
                    "url": w.get("doi") or loc.get("landing_page_url") or w.get("id") or "",
                    "abstract": _clip(" ".join(t for _, t in words), abstract_chars)})
    return out


def search_arxiv(query: str, max_results: int, abstract_chars: int,
                 timeout: float = 15.0) -> List[Dict[str, Any]]:
    from urllib.parse import quote

    # every term searched in all fields and ANDed; "all:a b" would scope only the first term
    terms = [t for t in query.replace(":", " ").split() if t][:8] or [query]
    sq = "+AND+".join(f"all:{quote(t, safe='')}" for t in terms)
    r = _get(f"{ARXIV_URL}?search_query={sq}&start=0&max_results={int(max_results)}"
             f"&sortBy=relevance", timeout)
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        published = (e.findtext(f"{_ATOM}published") or "")[:4]
        out.append({"title": _clip(e.findtext(f"{_ATOM}title"), 300),
                    "year": int(published) if published.isdigit() else None, "venue": "arXiv",
                    "citations": None, "url": (e.findtext(f"{_ATOM}id") or "").strip(),
                    "abstract": _clip(e.findtext(f"{_ATOM}summary"), abstract_chars)})
    return out


class PaperSearchToolSet(ToolSet):
    """Search papers by topic: Semantic Scholar, then OpenAlex, then arXiv, until one answers."""

    def __init__(self, name: str, abstract_chars: int = 1200, timeout: float = 15.0, **kwargs):
        super().__init__(name, **kwargs)
        self.abstract_chars = abstract_chars
        self.timeout = timeout

    @tool
    async def paper_search(self, query: str, max_results: int = 8) -> Dict[str, Any]:
        """
        Search the scholarly literature (Semantic Scholar, falling back to OpenAlex and arXiv).

        Args:
            query: Topic keywords, e.g. "density-preserving dimensionality reduction single-cell benchmark"
            max_results: Maximum number of papers to return (default 8, at most 20)

        Returns:
            dict with the papers found: title, year, venue, citations, url, abstract
        """
        n = max(1, min(int(max_results), 20))
        errors = []
        for source, fn in (("semantic_scholar", search_semantic_scholar),
                           ("openalex", search_openalex), ("arxiv", search_arxiv)):
            try:
                papers = await asyncio.to_thread(fn, query, n, self.abstract_chars, self.timeout)
            except Exception as e:  # noqa: BLE001 — rate limit, network, parse: try the next source
                errors.append(f"{source}: {type(e).__name__}: {str(e)[:120]}")
                logger.info(f"paper_search {source} failed for {query!r}: {errors[-1]}")
                continue
            if papers:
                return {"success": True, "query": query, "source": source,
                        "results": papers, "total_results": len(papers)}
            errors.append(f"{source}: no results")
        return {"success": False, "query": query, "results": [], "total_results": 0,
                "error": "; ".join(errors)}


__all__ = ["PaperSearchToolSet", "search_semantic_scholar", "search_openalex", "search_arxiv"]
