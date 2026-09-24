"""LabNotebook's research step: a PantheonOS agent that reads up on the problem before the first
notebook is written.

The agent gets the problem statement and the seed program -- the same inputs as the Understand
call -- and three tools: DuckDuckGo web search, page fetch, and scholarly search (Semantic
Scholar, falling back to arXiv). It searches, reads and refines until it writes cited notes, which
LabNotebook puts into the Understand prompt. Everything is bounded (wall clock, agent messages, time
per tool call, characters per tool result) and every failure degrades to "no notes": the method
then runs exactly as without research. The agent runs on the method's own model through the same
endpoint as every other call, so a run's clock and budget cover it.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

RESEARCH_SYSTEM = (
    "You are a research assistant preparing background for a scientist who is about to attack an "
    "optimisation problem by writing and improving a program. Before any experiment is run, find "
    "out: (1) what each part of the objective rewards and how it is computed, from the problem "
    "statement and from the literature on that measure; (2) which methods lead published "
    "benchmarks on this or closely related problems, with their numbers; (3) concrete recipes and "
    "parameter settings of the strongest methods, and their known failure modes; (4) engineering "
    "facts that matter under the stated constraints (allowed libraries, time limits, data size). "
    "Use web_search for pages, documentation, benchmark leaderboards and code; paper_search for "
    "papers; web_fetch to read the most relevant pages in full. Prefer primary sources. Never "
    "invent a source or a number. Search broadly first, then read the best few sources. When you "
    "have enough, stop calling tools and reply with notes under EXACTLY these headings: "
    "## How the score is built / ## What published benchmarks show / ## Method recipes / "
    "## Engineering / ## Sources. Cite every claim as [n] and list the numbered sources (title, "
    "URL) under ## Sources. No code. At most 800 words before the source list."
)

RESEARCH_FINISH = (
    "Stop searching now. Using only what you have found so far, write the notes under EXACTLY the "
    "headings ## How the score is built / ## What published benchmarks show / ## Method recipes / "
    "## Engineering / ## Sources, citing every claim as [n]. No code. At most 800 words before "
    "the source list."
)

NOTES_HEADINGS = ("## How the score is built", "## Method recipes")


def _blocked(url: str, block: Sequence[str]) -> bool:
    u = (url or "").lower()
    return any(b and b.lower() in u for b in block)


class ResearchToolSet(ToolSet):
    """Web search, page fetch and paper search for the research agent, with a URL block list and a
    log of every query and URL (the audit trail a benchmark needs)."""

    def __init__(self, name: str, block: Sequence[str] = (), fetch_timeout: float = 20.0,
                 abstract_chars: int = 1200, **kwargs):
        super().__init__(name, **kwargs)
        from pantheon.toolsets.paper_search import PaperSearchToolSet
        from pantheon.toolsets.web_urllib import WebToolSet

        self._web = WebToolSet(f"{name}-web")
        self._papers = PaperSearchToolSet(f"{name}-papers", abstract_chars=abstract_chars,
                                          timeout=fetch_timeout)
        self.block = [b for b in block if b]
        self.fetch_timeout = fetch_timeout
        self.log: Dict[str, Any] = {"web_queries": [], "paper_queries": [], "fetched": [],
                                    "blocked": [], "tool_errors": 0}

    def _drop_blocked(self, results: List[Dict[str, Any]], key: str = "url") -> List[Dict[str, Any]]:
        kept = []
        for r in results or []:
            u = str(r.get(key) or r.get("href") or r.get("link") or "")
            if _blocked(u, self.block):
                self.log["blocked"].append(u)
                continue
            kept.append(r)
        return kept

    @tool
    async def web_search(self, query: str, max_results: int = 6) -> Dict[str, Any]:
        """
        Search the web with DuckDuckGo: documentation, benchmark leaderboards, blog posts, code,
        method pages. Returns titles, snippets and URLs.

        Args:
            query: Search query
            max_results: Maximum number of results (default 6, at most 10)

        Returns:
            dict with the results (title, url, snippet)
        """
        self.log["web_queries"].append(query)
        try:
            r = await self._web.web_search(query, max(1, min(int(max_results), 10)))
        except Exception as e:  # noqa: BLE001
            self.log["tool_errors"] += 1
            return {"success": False, "query": query, "results": [], "error": f"{type(e).__name__}: {e}"}
        if isinstance(r, dict):
            r = dict(r)
            r["results"] = self._drop_blocked(r.get("results") or [])
            r["total_results"] = len(r["results"])
        return r

    @tool
    async def paper_search(self, query: str, max_results: int = 8) -> Dict[str, Any]:
        """
        Search the scholarly literature (Semantic Scholar, falling back to arXiv). Returns title,
        year, venue, citation count, URL and abstract of each paper.

        Args:
            query: Topic keywords
            max_results: Maximum number of papers (default 8, at most 20)

        Returns:
            dict with the papers found
        """
        self.log["paper_queries"].append(query)
        try:
            r = await self._papers.paper_search(query, max_results)
        except Exception as e:  # noqa: BLE001
            self.log["tool_errors"] += 1
            return {"success": False, "query": query, "results": [], "error": f"{type(e).__name__}: {e}"}
        if isinstance(r, dict):
            r = dict(r)
            r["results"] = self._drop_blocked(r.get("results") or [])
            r["total_results"] = len(r["results"])
        return r

    @tool
    async def web_fetch(self, url: str) -> Dict[str, Any]:
        """
        Fetch a web page and return its text (converted to markdown).

        Args:
            url: The URL to read

        Returns:
            dict with the page content
        """
        if _blocked(url, self.block):
            self.log["blocked"].append(url)
            return {"success": False, "url": url, "error": "this source is not available"}
        self.log["fetched"].append(url)
        try:
            return await asyncio.wait_for(self._web.web_fetch(url), timeout=self.fetch_timeout)
        except Exception as e:  # noqa: BLE001 -- a timeout included
            self.log["tool_errors"] += 1
            return {"success": False, "url": url, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def final_notes(messages: List[Dict[str, Any]]) -> str:
    """The agent's final answer: the last assistant message without tool calls, if it is notes."""
    for m in reversed(messages or []):
        if m.get("role") != "assistant":
            continue
        if m.get("tool_calls"):
            return ""
        text = m.get("content")
        if isinstance(text, list):
            text = "\n".join(str(b.get("text", "")) for b in text if isinstance(b, dict))
        text = (text or "").strip()
        return text if any(h in text for h in NOTES_HEADINGS) else ""
    return ""


def _material(messages: List[Dict[str, Any]], limit: int) -> str:
    """What the agent read (tool results), for a fallback write-up when the agent itself did not
    finish: the newest material last, clipped to `limit` characters in all."""
    parts = []
    for m in messages or []:
        if m.get("role") == "tool":
            c = m.get("content")
            parts.append(c if isinstance(c, str) else json.dumps(c, default=str))
    text = "\n\n---\n\n".join(parts)
    return text[-limit:]


async def run_research(*, model: str, objective: str, seed_text: str,
                       model_params: Optional[Dict[str, Any]] = None,
                       secs: float = 900.0, turns: int = 40, finish_secs: float = 180.0,
                       tool_chars: int = 8000, tool_secs: float = 20.0,
                       block: Sequence[str] = (),
                       fallback_llm: Optional[Callable[[str, str], Awaitable[str]]] = None,
                       ) -> Tuple[str, Dict[str, Any]]:
    """Run the research agent; return (notes, log). Never raises."""
    t0 = time.time()
    # usd_est: PantheonOS's catalog price for the agent's calls, which can be far from what an
    # endpoint bills (measured 2026-09-24 through a metering proxy: $0.205 estimated, $0.024
    # billed for the same 10 calls); a gateway's own ledger is the authority on spend
    log: Dict[str, Any] = {"stopped": "", "secs": 0.0, "calls": 0, "tokens": 0, "usd_est": 0.0,
                           "notes_chars": 0, "finish": ""}
    tools: Optional[ResearchToolSet] = None
    messages: List[Dict[str, Any]] = []
    notes = ""
    try:
        from pantheon.agent import Agent
        from pantheon.internal.memory import Memory

        agent = Agent(name="lab-notebook-research", instructions=RESEARCH_SYSTEM, model=model,
                      model_params=dict(model_params or {}), use_memory=True,
                      tool_timeout=int(tool_secs) + 5, max_tool_content_length=tool_chars)
        tools = ResearchToolSet("ln-research", block=block, fetch_timeout=tool_secs)
        await agent.toolset(tools)
        mem = Memory(name=f"ln-research-{int(t0)}")
        prompt = (f"# Problem\n{objective}\n\n# Seed program\n```\n{seed_text}\n```\n\n"
                  "Research this problem and write the notes.")
        log["stopped"] = "answered"
        try:
            await asyncio.wait_for(agent.run(prompt, memory=mem, max_turns=turns),
                                   timeout=max(1.0, secs - finish_secs))
        except asyncio.TimeoutError:
            log["stopped"] = "clock"
        except Exception as e:  # noqa: BLE001
            log["stopped"] = f"error: {type(e).__name__}: {str(e)[:200]}"
        messages = mem.get_messages(for_llm=False)
        notes = final_notes(messages)
        if not notes:
            if log["stopped"] == "answered":
                log["stopped"] = "turns"
            remaining = secs - (time.time() - t0)
            if remaining > 2:
                # the agent stopped mid-search (turn cap, clock) or answered without notes: one
                # more turn with the tools off, on the conversation so far; half the time left is
                # kept for the fallback write-up in case this turn fails too
                try:
                    mem.ensure_fixed()
                    await asyncio.wait_for(agent.run(RESEARCH_FINISH, memory=mem, tool_use=False,
                                                     max_turns=1),
                                           timeout=remaining * (0.5 if fallback_llm else 1.0))
                    messages = mem.get_messages(for_llm=False)
                    notes = final_notes(messages)
                    log["finish"] = "agent" if notes else "agent-empty"
                except Exception as e:  # noqa: BLE001 -- a timeout included
                    log["finish"] = f"agent-failed: {type(e).__name__}"
        if not notes and fallback_llm is not None and messages:
            remaining = secs - (time.time() - t0)
            if remaining > 2:
                try:
                    text = await asyncio.wait_for(fallback_llm(
                        RESEARCH_SYSTEM,
                        f"# Problem\n{objective}\n\n# What was found\n{_material(messages, 60000)}\n\n"
                        + RESEARCH_FINISH), timeout=remaining)
                    notes = text.strip() if any(h in (text or "") for h in NOTES_HEADINGS) else ""
                    log["finish"] = "fallback" if notes else "fallback-empty"
                except Exception as e:  # noqa: BLE001
                    log["finish"] = f"fallback-failed: {type(e).__name__}"
    except Exception as e:  # noqa: BLE001 -- the step must never break the run
        logger.warning(f"lab_notebook research failed: {type(e).__name__}: {e}")
        log["stopped"] = f"error: {type(e).__name__}: {str(e)[:200]}"
    for m in messages:
        if m.get("role") == "assistant":
            meta = m.get("_metadata") or {}
            log["calls"] += 1
            log["tokens"] += int(meta.get("total_tokens", 0) or 0)
            log["usd_est"] += float(meta.get("current_cost", 0.0) or 0.0)
    if tools is not None:
        log.update(tools.log)
    log["secs"] = round(time.time() - t0, 1)
    log["notes_chars"] = len(notes)
    log["messages"] = len(messages)
    return notes, log
