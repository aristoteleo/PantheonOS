"""LabNotebook's research step: a PantheonOS agent that reads up on the problem before the first
notebook is written.

The agent gets the problem statement and the seed program -- the same inputs as the Understand
call -- and three tools: DuckDuckGo web search, a browser (crawl4ai/Playwright: JavaScript pages
such as leaderboards render, long pages are read by window or by `find`, static fetch as the
fallback) and scholarly search (Semantic Scholar, OpenAlex, arXiv). Its instructions point it at
evidence about methods -- published results with numbers, winning variants, recipes, measures that
do not discriminate -- rather than at the metric's definition, which the problem already gives
(measured 2026-09-24: with the definition-first prompt and no browser, three runs spent their turns
on metric source code and could not read the leaderboard). The work is split into two
agents with their own budgets, because one agent asked for both ran out of turns reading
benchmarks before it read a single method paper (measured 2026-09-24): an EVIDENCE agent finds the
benchmark's overall ranking, the per-measure numbers and where the leaders gain over the seed, and
names the top method families; a RECIPE agent, given that, reads the method papers, documentation
and reference implementations for recipes detailed enough to implement from scratch. Their notes
are joined (source numbers continue) and LabNotebook puts them into the Understand prompt. Everything is bounded (wall clock, agent messages, time
per tool call, characters per tool result) and every failure degrades to "no notes": the method
then runs exactly as without research. The agent runs on the method's own model through the same
endpoint as every other call, so a run's clock and budget cover it.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

_BROWSING = (
    "Tools: web_search (DuckDuckGo) for pages, documentation, leaderboards and code; paper_search "
    "for papers; web_browse to read a page in a real browser (JavaScript runs, so leaderboards and "
    "other dynamic pages show their content; use its `find` argument to jump to a method name or a "
    "table column and `start` to page through long pages). Prefer primary sources. Never invent a "
    "source or a number: every number you write must be one you read in a tool result, and every "
    "claim is cited as [n]."
)

EVIDENCE_SYSTEM = (
    "You are a research assistant preparing background for a scientist who is about to attack an "
    "optimisation problem by writing and improving a program. The problem statement defines the "
    "objective; do not re-derive how it is computed. Your job in this step is EVIDENCE ABOUT "
    "METHODS: find published results on this objective or on benchmarks of the same measures "
    "(leaderboards, results tables, benchmark papers) and extract, with numbers:\n"
    "1. the benchmark's OVERALL ranking of methods (its order, with overall scores where shown) and "
    "which variant of each leading method ranks best (input representation, preprocessing, "
    "settings). Where a method is run in several variants, compare the variants on the overall "
    "score -- from the page, or from the benchmark's results data files (JSON or CSV behind the "
    "page, often linked from it or in its repository) when the page does not print it -- and "
    "name the best variant with its number; do not infer it from one dataset or one measure;\n"
    "2. the per-measure numbers that separate the leaders from the rest;\n"
    "3. for each measure the problem scores, the seed's value as the problem statement gives it "
    "and the leaders' values on the same scale where the benchmark gives them, so the gains can be "
    "read off; say plainly where published methods do not differ.\n"
    "The objective is usually a combination (a mean, say) of measures, so the overall ranking is "
    "the best guide. Control or baseline entries (built from the reference data itself, such as "
    "'true features', 'random', or rows marked as controls) mark ceilings and floors; they are not "
    "methods. " + _BROWSING + " When you have enough, stop calling tools and reply under EXACTLY "
    "these headings: ## Overall ranking / ## Per-measure evidence / ## Where the leaders gain over "
    "the seed / ## Top three method families (one line each: family, best-ranked variant, why) / "
    "## Sources (numbered: title, URL). No code. At most 700 words before the source list."
)

EVIDENCE_FINISH = (
    "Stop searching now. Using only what you have found, reply under EXACTLY the headings "
    "## Overall ranking / ## Per-measure evidence / ## Where the leaders gain over the seed / "
    "## Top three method families / ## Sources, citing every claim as [n]; every number from a tool "
    "result; no code; at most 700 words before the source list."
)

RECIPE_SYSTEM = (
    "You are a research assistant preparing background for a scientist who is about to attack an "
    "optimisation problem by writing and improving a program. An earlier step found which methods "
    "lead on this objective; its notes are in the message. Your job in this step is RECIPES: for "
    "each of the top method families it names, find how to implement the method well under the "
    "problem's constraints. If the constraints rule out ready-made implementations, the recipe must "
    "be detailed enough to implement from scratch: the objective, the gradient or update rule, the "
    "optimiser and its schedule (learning rate, number of iterations, early or late phases), the "
    "initialisation, neighbourhood or kernel parameters, and the default of every parameter; plus "
    "known failure modes. Read the method papers (open versions: arXiv, bioRxiv, PubMed Central), "
    "practical guides by the method authors, official documentation and the reference "
    "implementations' source code (a default in code is authoritative). If you cannot find a "
    "value, say so rather than guess.\n"
    "FEASIBILITY matters as much as rank. Reference implementations often rely on compiled code "
    "(numba, C++, GPU) or on libraries the problem forbids; a method that wins a benchmark can be "
    "slow, fragile or hard to get right when rewritten with the allowed libraries. For each family, "
    "estimate what a straightforward implementation with the allowed libraries costs at the "
    "problem's data size and time limit (cost per iteration times iterations; whether the update "
    "can be written as dense array operations or needs per-element loops), and say how hard it is "
    "to get right. Then give the SIMPLEST variant that keeps the mechanism that makes the family "
    "win (for example the same key term added to a simpler base objective whose gradient is a few "
    "array operations), and name published variants of that kind when they exist, with their "
    "parameters. When the simplest variant builds on another method (a base objective), give that "
    "base method's complete recipe and parameter defaults too. Recipes use the input representation of the best-ranked variant in the evidence "
    "notes, not the preprocessing of a benchmark's wrapper script.\n"
    "PROTOTYPE before you recommend. run_python_script runs a self-contained Python script in a "
    "fresh process (numpy, scipy, scikit-learn available; hard time limit per call). Write the "
    "plain version of your first recommendation using only the libraries the problem allows, run "
    "it on synthetic data of the problem's stated size (for example a mixture of Gaussian clusters "
    "with the stated number of rows and a few thousand columns), check that the output is finite "
    "and sensible, and time it against the problem's time limit; if time allows, compare two "
    "candidate recipes on the synthetic data with a quality measure related to the objective "
    "(measuring tools outside the allowed libraries are fine for that). Put the measured runtimes "
    "and results in the notes, marked as measurements on synthetic data. A recipe you measured to "
    "fail or to exceed the limit is not a first recommendation.\n" + _BROWSING + " When you "
    "have enough, stop calling tools and reply under EXACTLY these headings: ## Method recipes "
    "(one subsection per family, each with its feasibility under the constraints and its simplest "
    "effective variant) / ## What to try first (ordered by expected gain for the effort; the first "
    "item must be implementable with the allowed libraries in about 200 lines and must run well "
    "inside the time limit at the stated data size; every item backed by a published METHOD's "
    "result, never by a control's score, since a control is built from the reference itself) / "
    "## Engineering (what matters under the stated constraints: allowed libraries, time limits, "
    "data size) / ## Sources (numbered: title, URL). Number your sources from the number given in "
    "the message. No code. At most 1000 words before the source list."
)

RECIPE_FINISH = (
    "Stop searching now. Using only what you have found, reply under EXACTLY the headings "
    "## Method recipes (each with its feasibility under the constraints and its simplest effective "
    "variant) / ## What to try first (by expected gain for the effort; the first item implementable "
    "with the allowed libraries in about 200 lines and fast enough for the time limit; each item "
    "backed by a published method's result, not a control's) / ## Engineering / ## Sources, "
    "numbering sources from "
    "the number given earlier; every number and default from a tool result; no code; at most 900 "
    "words before the source list."
)

SYNTH_SYSTEM = (
    "You are writing the prior-knowledge brief that a scientist will read before writing a "
    "program for the problem below. You are given research notes (benchmark evidence and method "
    "recipes, with numbered sources). Distil them into a brief of at most 700 words under EXACTLY "
    "these headings, in this order:\n"
    "## What the score rewards -- which measures carry the score and where the seed is weak, with "
    "the numbers from the notes.\n"
    "## Recommended approach -- ONE approach: the simplest variant of the best-supported method "
    "that the notes judge feasible under the problem's constraints. Give a complete recipe: the "
    "input representation (that of the best-ranked variant), the objective, the optimiser and its "
    "schedule, the initialisation, and every parameter with its value.\n"
    "## Alternatives -- at most three, one line each, with their evidence.\n"
    "## Engineering -- the constraints and costs that decide feasibility, with any runtimes the "
    "notes measured on synthetic data.\n"
    "## Pitfalls -- measures not worth chasing and known failure modes.\n"
    "## Sources -- only the sources you cite, keeping their numbers.\n"
    "Keep only claims the notes support, with their [n] citations; add nothing the notes do not "
    "contain. No code.\n\n" + """Rules for the recommended approach:
1. Feasibility first. If the best-ranked method is, by the notes' feasibility analysis, slow or
   hard to implement well with the allowed libraries (for example it relies on per-element
   stochastic updates that the reference runs as compiled code), do not recommend that
   implementation: recommend the mechanism that makes it win on a simpler base the notes judge
   feasible (a published variant of that kind if the notes name one), and list the full method
   under Alternatives.
2. Input: the input representation of the best-ranked variant in the evidence, even if the seed
   program uses another one.
3. Completeness: every parameter of the recipe has a value taken from the notes (paper, docs or
   reference code); for a parameter the notes lack, say so explicitly.
3b. Measurements: prefer the approach the notes measured to run correctly within the time limit;
   never recommend one the notes measured to fail or to exceed it.
4. Evidence: the approach is backed by a published method's result, never by a control's."""
)
SYNTH_HEADINGS = ("## Recommended approach",)

REVIEW_SYSTEM = (
    "You review a prior-knowledge brief against the research notes it was distilled from. Check it "
    "rule by rule and fix every violation:\n" + SYNTH_SYSTEM.split("Rules for the recommended approach:")[1] +
    "\n5. Every claim and number in the brief is in the notes, with its citation.\n"
    "Reply with the corrected brief only, under the same headings (## What the score rewards / "
    "## Recommended approach / ## Alternatives / ## Engineering / ## Pitfalls / ## Sources), at "
    "most 700 words, no code, no commentary about the review."
)

EVIDENCE_HEADINGS = ("## Overall ranking", "## Top three method families")
RECIPE_HEADINGS = ("## Method recipes",)
NOTES_HEADINGS = EVIDENCE_HEADINGS + RECIPE_HEADINGS


def _blocked(url: str, block: Sequence[str]) -> bool:
    u = (url or "").lower()
    return any(b and b.lower() in u for b in block)


class ResearchToolSet(ToolSet):
    """Web search, a browser and paper search for the research agent, with a URL block list and a
    log of every query and URL (the audit trail a benchmark needs)."""

    def __init__(self, name: str, block: Sequence[str] = (), fetch_timeout: float = 20.0,
                 abstract_chars: int = 1200, page_chars: int = 7000,
                 browse_timeout: float = 60.0, **kwargs):
        super().__init__(name, **kwargs)
        from pantheon.toolsets.paper_search import PaperSearchToolSet
        from pantheon.toolsets.web import WebToolSet as BrowserToolSet
        from pantheon.toolsets.web_urllib import WebToolSet

        self._web = WebToolSet(f"{name}-web")
        self._browser = BrowserToolSet(f"{name}-browser")   # crawl4ai: Playwright/Chromium
        self.page_chars = page_chars
        self.browse_timeout = browse_timeout
        self._pages: Dict[str, Dict[str, Any]] = {}
        self._papers = PaperSearchToolSet(f"{name}-papers", abstract_chars=abstract_chars,
                                          timeout=fetch_timeout)
        self.block = [b for b in block if b]
        self.fetch_timeout = fetch_timeout
        self.log: Dict[str, Any] = {"web_queries": [], "paper_queries": [], "fetched": [],
                                    "blocked": [], "tool_errors": 0, "rendered": 0, "static": 0,
                                    "tool_secs": []}

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
        t0 = time.time()
        try:
            # bounded: a throttled DuckDuckGo made ddgs retry for minutes inside an agent turn
            r = await asyncio.wait_for(self._web.web_search(query, max(1, min(int(max_results), 10))),
                                       timeout=self.fetch_timeout)
        except Exception as e:  # noqa: BLE001 -- a timeout included
            self.log["tool_errors"] += 1
            return {"success": False, "query": query, "results": [], "error": f"{type(e).__name__}: {e}"}
        finally:
            self.log["tool_secs"].append(["web_search", round(time.time() - t0, 1)])
        if isinstance(r, dict):
            r = dict(r)
            r["results"] = self._drop_blocked(r.get("results") or [])
            r["total_results"] = len(r["results"])
        return r

    @tool
    async def paper_search(self, query: str, max_results: int = 8) -> Dict[str, Any]:
        """
        Search the scholarly literature (Semantic Scholar, falling back to OpenAlex and arXiv). Returns title,
        year, venue, citation count, URL and abstract of each paper.

        Args:
            query: Topic keywords
            max_results: Maximum number of papers (default 8, at most 20)

        Returns:
            dict with the papers found
        """
        self.log["paper_queries"].append(query)
        t0 = time.time()
        try:
            # bounded: three sources with retries and backoff can add up to minutes
            r = await asyncio.wait_for(self._papers.paper_search(query, max_results),
                                       timeout=1.5 * self.fetch_timeout)
        except Exception as e:  # noqa: BLE001 -- a timeout included
            self.log["tool_errors"] += 1
            return {"success": False, "query": query, "results": [], "error": f"{type(e).__name__}: {e}"}
        finally:
            self.log["tool_secs"].append(["paper_search", round(time.time() - t0, 1)])
        if isinstance(r, dict):
            r = dict(r)
            r["results"] = self._drop_blocked(r.get("results") or [])
            r["total_results"] = len(r["results"])
        return r

    async def _load(self, url: str) -> Dict[str, Any]:
        """The page's text: rendered in a browser (JavaScript pages, leaderboards), else fetched
        statically; cached so paging and `find` do not load it again."""
        if url in self._pages:
            return self._pages[url]
        text, how, err = "", "", ""
        try:
            out = await asyncio.wait_for(self._browser.web_crawl([url], timeout=self.browse_timeout),
                                         timeout=self.browse_timeout + 15)
            text = (out[0] if out else "") or ""
            how = "browser"
        except Exception as e:  # noqa: BLE001 -- no browser in this environment, a timeout
            err = f"browser: {type(e).__name__}: {str(e)[:120]}"
        if len(text.strip()) < 200:
            try:
                r = await asyncio.wait_for(self._web.web_fetch(url), timeout=self.fetch_timeout)
                if isinstance(r, dict) and r.get("success", True):
                    t2 = str(r.get("content") or r.get("text") or "")
                    if len(t2) > len(text):
                        text, how = t2, "static"
            except Exception as e:  # noqa: BLE001
                err += f"; static: {type(e).__name__}: {str(e)[:120]}"
        page = {"text": text, "how": how, "error": err}
        if text:
            self._pages[url] = page
            self.log["rendered" if how == "browser" else "static"] += 1
        return page

    @tool
    async def web_browse(self, url: str, find: str = "", start: int = 0) -> Dict[str, Any]:
        """
        Open a web page in a browser (JavaScript is executed, so leaderboards and other dynamic
        pages show their content) and return its text as markdown, one window at a time.

        Args:
            url: The URL to open
            find: Optional text to look for (case-insensitive), e.g. a method name or a table
                column; the result is then the passages around its first matches instead of a window
            start: Character offset of the window to return (for paging through a long page)

        Returns:
            dict with the text (or the passages found), the page length and the next offset
        """
        if _blocked(url, self.block):
            self.log["blocked"].append(url)
            return {"success": False, "url": url, "error": "this source is not available"}
        self.log["fetched"].append(url)
        t0 = time.time()
        page = await self._load(url)
        self.log["tool_secs"].append(["web_browse", round(time.time() - t0, 1)])
        text = page["text"]
        if not text:
            self.log["tool_errors"] += 1
            return {"success": False, "url": url, "error": page["error"] or "empty page"}
        n = len(text)
        if find:
            low, key, hits, pos = text.lower(), find.lower(), [], 0
            while len(hits) < 4:
                i = low.find(key, pos)
                if i < 0:
                    break
                hits.append(text[max(0, i - 600): i + 1400])
                pos = i + 1400
            return {"success": True, "url": url, "length": n, "find": find, "matches": len(hits),
                    "passages": hits or ["(no match)"]}
        start = max(0, min(int(start), n))
        end = min(n, start + self.page_chars)
        return {"success": True, "url": url, "length": n, "start": start, "text": text[start:end],
                "next_start": end if end < n else None}


def final_notes(messages: List[Dict[str, Any]], headings: Sequence[str] = NOTES_HEADINGS) -> str:
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
        return text if any(h in text for h in headings) else ""
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


def count_sources(notes: str) -> int:
    """The highest source number listed under the notes' ## Sources heading."""
    i = notes.rfind("## Sources")
    nums = [int(x) for x in re.findall(r"(?m)^\s*\[?(\d+)[\].]", notes[i:])] if i >= 0 else []
    return max(nums) if nums else 0


async def _phase(*, name: str, model: str, model_params: Dict[str, Any], system: str,
                 finish: str, headings: Sequence[str], prompt: str, secs: float, turns: int,
                 finish_secs: float, tool_chars: int, tool_secs: float, block: Sequence[str],
                 fallback_llm: Optional[Callable[[str, str], Awaitable[str]]],
                 sandbox: bool = False) -> Tuple[str, Dict[str, Any]]:
    """One research agent: search until it answers, the message cap or the clock (less the time
    kept for writing up); a failed model call is continued on the same conversation, twice at
    most; no answer gets one tool-free write-up turn, then a write-up from the material on the
    method's own client. Never raises."""
    t0 = time.time()
    log: Dict[str, Any] = {"stopped": "", "resumed": 0, "finish": "", "calls": 0, "tokens": 0,
                           "usd_est": 0.0}
    tools: Optional[ResearchToolSet] = None
    runner = None
    messages: List[Dict[str, Any]] = []
    notes = ""
    try:
        from pantheon.agent import Agent
        from pantheon.internal.memory import Memory

        agent = Agent(name=f"lab-notebook-{name}", instructions=system, model=model,
                      model_params=dict(model_params or {}), use_memory=True,
                      tool_timeout=int(tool_secs) + 90, max_tool_content_length=tool_chars)
        tools = ResearchToolSet(f"ln-{name}", block=block, fetch_timeout=tool_secs)
        await agent.toolset(tools)
        runner = None
        if sandbox:
            from pantheon.toolsets.python.sandbox import SandboxPythonToolSet

            runner = SandboxPythonToolSet(f"ln-{name}-python", max_timeout_s=300)
            await agent.toolset(runner)
        mem = Memory(name=f"ln-{name}-{int(t0)}")
        msg = prompt
        while True:
            used = len(mem.get_messages(for_llm=False))
            try:
                await asyncio.wait_for(agent.run(msg, memory=mem, max_turns=max(2, turns - used)),
                                       timeout=max(1.0, secs - finish_secs - (time.time() - t0)))
                log["stopped"] = "answered"
                break
            except asyncio.TimeoutError:
                log["stopped"] = "clock"
                break
            except Exception as e:  # noqa: BLE001
                log["stopped"] = f"error: {type(e).__name__}: {str(e)[:200]}"
                # a failed model call (measured: an upstream provider failing mid-stream, which
                # OpenRouter reports as an error event inside a 200 stream) ends Agent.run; the
                # conversation so far is kept and the agent continues from it
                if (log["resumed"] >= 2 or secs - finish_secs - (time.time() - t0) < 60
                        or len(mem.get_messages(for_llm=False)) >= turns):
                    break
                log["resumed"] += 1
                mem.ensure_fixed()
                msg = "Continue the research from where you left off."
        messages = mem.get_messages(for_llm=False)
        notes = final_notes(messages, headings)
        if not notes:
            if log["stopped"] == "answered":
                log["stopped"] = "turns"
            remaining = secs - (time.time() - t0)
            if remaining > 2:
                # half the time left is kept for the fallback write-up in case this turn fails
                try:
                    mem.ensure_fixed()
                    await asyncio.wait_for(agent.run(finish, memory=mem, tool_use=False, max_turns=1),
                                           timeout=remaining * (0.5 if fallback_llm else 1.0))
                    messages = mem.get_messages(for_llm=False)
                    notes = final_notes(messages, headings)
                    log["finish"] = "agent" if notes else "agent-empty"
                except Exception as e:  # noqa: BLE001 -- a timeout included
                    log["finish"] = f"agent-failed: {type(e).__name__}"
        if not notes and fallback_llm is not None and messages:
            remaining = secs - (time.time() - t0)
            if remaining > 2:
                try:
                    text = await asyncio.wait_for(fallback_llm(
                        system, f"{prompt}\n\n# What was found\n{_material(messages, 60000)}\n\n"
                        + finish), timeout=remaining)
                    notes = text.strip() if any(h in (text or "") for h in headings) else ""
                    log["finish"] = "fallback" if notes else "fallback-empty"
                except Exception as e:  # noqa: BLE001
                    log["finish"] = f"fallback-failed: {type(e).__name__}"
    except Exception as e:  # noqa: BLE001 -- the step must never break the run
        logger.warning(f"lab_notebook research ({name}) failed: {type(e).__name__}: {e}")
        log["stopped"] = f"error: {type(e).__name__}: {str(e)[:200]}"
    for m in messages:
        if m.get("role") == "assistant":
            meta = m.get("_metadata") or {}
            log["calls"] += 1
            log["tokens"] += int(meta.get("total_tokens", 0) or 0)
            log["usd_est"] += float(meta.get("current_cost", 0.0) or 0.0)
    if tools is not None:
        log.update(tools.log)
    if runner is not None:
        log["python_runs"] = list(runner.log)
    log["secs"] = round(time.time() - t0, 1)
    log["notes_chars"] = len(notes)
    log["messages"] = len(messages)
    return notes, log


async def run_research(*, model: str, objective: str, seed_text: str,
                       model_params: Optional[Dict[str, Any]] = None,
                       secs: float = 1200.0, turns: int = 120, finish_secs: float = 180.0,
                       tool_chars: int = 9000, tool_secs: float = 20.0,
                       block: Sequence[str] = (),
                       fallback_llm: Optional[Callable[[str, str], Awaitable[str]]] = None,
                       ) -> Tuple[str, Dict[str, Any]]:
    """The two research agents, evidence then recipes, within `secs` and `turns` in all (the
    evidence agent gets half of each, the recipe agent what is left); return (notes, log). The
    notes are the evidence notes followed by the recipe notes; if one agent produced nothing, the
    other's notes stand alone. Never raises.

    usd_est in the log is PantheonOS's catalog price for the agents' calls, which can be far from
    what an endpoint bills (measured 2026-09-24 through a metering proxy: $0.205 estimated, $0.024
    billed for the same 10 calls); a gateway's own ledger is the authority on spend."""
    t0 = time.time()
    problem = f"# Problem\n{objective}\n\n# Seed program\n```\n{seed_text}\n```\n\n"
    common = dict(model=model, model_params=dict(model_params or {}), tool_chars=tool_chars,
                  tool_secs=tool_secs, block=block, fallback_llm=fallback_llm)
    # the brief is written last, on the method's own client, from the two agents' notes
    synth_secs = min(180.0, secs / 5.0) if fallback_llm is not None else 0.0
    secs = secs - synth_secs
    ev_secs = secs / 2.0
    evidence, ev_log = await _phase(
        name="evidence", system=EVIDENCE_SYSTEM, finish=EVIDENCE_FINISH, headings=EVIDENCE_HEADINGS,
        prompt=problem + "Find the published evidence about methods and write the notes.",
        secs=ev_secs, turns=max(4, turns // 2), finish_secs=finish_secs / 2.0, **common)
    left = secs - (time.time() - t0)
    k = count_sources(evidence)
    recipes, rc_log = await _phase(
        name="recipes", system=RECIPE_SYSTEM, finish=RECIPE_FINISH, headings=RECIPE_HEADINGS,
        sandbox=True,
        prompt=problem + "# Evidence notes from the earlier step\n"
        + (evidence or "(the earlier step found nothing; identify the leading methods yourself "
                       "from benchmark results first, briefly)")
        + f"\n\nWrite the recipe notes. Number your sources from [{k + 1}].",
        secs=max(min(60.0, secs / 4.0), left),
        # each agent gets half the messages: with "whatever the evidence agent left", the recipe
        # agent hit its cap after 89 s with 10 calls (measured 2026-09-24, Sherlock)
        turns=max(4, turns // 2, turns - int(ev_log.get("messages", 0))),
        finish_secs=finish_secs / 2.0, **common)
    notes = "\n\n".join(x for x in (evidence, recipes) if x)
    log: Dict[str, Any] = {"evidence": ev_log, "recipes": rc_log, "synthesis": ""}
    # A brief, not the full notes, goes to the notebook: measured 2026-09-24 with best-of-N as the
    # reader, 16k characters of notes whose longest recipe came first led the program writer to
    # that recipe (a hand-written UMAP SGD, 96 of 117 programs, mean 0.19) even when the notes'
    # own first recommendation was another method; the 4.5k-character hand prior with one
    # recommended recipe led it to that recipe. The full notes stay in the log.
    if notes and fallback_llm is not None:
        remaining = synth_secs + secs - (time.time() - t0)
        try:
            brief = await asyncio.wait_for(fallback_llm(
                SYNTH_SYSTEM, problem + "# Research notes\n" + notes + "\n\nWrite the brief."),
                timeout=max(30.0, remaining))
            brief = (brief or "").strip()
            if any(h in brief for h in SYNTH_HEADINGS):
                log["full_notes"] = notes
                log["synthesis"] = "brief"
                # one review pass against the rules (measured 2026-09-24: of three briefs, one
                # kept the seed's input against the evidence and one recommended an implementation
                # the notes had judged hard to get right)
                remaining = synth_secs + secs - (time.time() - t0)
                try:
                    reviewed = await asyncio.wait_for(fallback_llm(
                        REVIEW_SYSTEM, problem + "# Research notes\n" + notes
                        + "\n\n# Brief to review\n" + brief), timeout=max(30.0, remaining))
                    reviewed = (reviewed or "").strip()
                    if any(h in reviewed for h in SYNTH_HEADINGS):
                        log["draft_brief"] = brief
                        brief = reviewed
                        log["synthesis"] = "brief, reviewed"
                except Exception as e:  # noqa: BLE001 -- the draft stands
                    log["synthesis"] = f"brief (review failed: {type(e).__name__})"
                notes = brief
            else:
                log["synthesis"] = "no brief (headings missing); full notes kept"
        except Exception as e:  # noqa: BLE001 -- a timeout included; the full notes stand
            log["synthesis"] = f"failed: {type(e).__name__}; full notes kept"
    log["python_runs"] = list(rc_log.get("python_runs", []))
    for key in ("calls", "tokens", "usd_est", "messages", "rendered", "static", "tool_errors"):
        log[key] = sum(p.get(key, 0) or 0 for p in (ev_log, rc_log))
    for key in ("web_queries", "paper_queries", "fetched", "blocked"):
        log[key] = list(ev_log.get(key, [])) + list(rc_log.get(key, []))
    log["stopped"] = f"evidence: {ev_log.get('stopped')}; recipes: {rc_log.get('stopped')}"
    log["finish"] = ",".join(x for x in (ev_log.get("finish"), rc_log.get("finish")) if x)
    log["secs"] = round(time.time() - t0, 1)
    log["notes_chars"] = len(notes)
    log["evidence_chars"], log["recipe_chars"] = len(evidence), len(recipes)
    return notes, log
