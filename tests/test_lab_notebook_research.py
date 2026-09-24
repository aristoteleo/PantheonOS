"""LabNotebook's research step, on fakes: the agent's answer reaches the Understand prompt; a turn
cap, a clock and an agent failure each degrade to a write-up or to no notes, never to a crash;
the block list hides sources; notes survive a checkpoint; the prior file is honoured."""
from __future__ import annotations

import asyncio
import json
from typing import List

import pytest

from pantheon.evolution.core import Budget, CodeGenome
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods import LabNotebook
from pantheon.evolution.methods import lab_notebook_research as R
from pantheon.evolution.methods.lab_notebook import CODE

EVIDENCE = ("## Overall ranking\nA first, B second [1]\n## Per-measure evidence\nA leads [1]\n"
            "## Where the leaders gain over the seed\ndensity +0.6 [1]\n"
            "## Top three method families\nA; B; C\n## Sources\n1. Leaderboard, https://lb.org/x")
RECIPES = ("## Method recipes\n### A\nuse A with k=30 [2]\n## What to try first\nA\n## Engineering\nfast\n"
           "## Sources\n2. A paper, https://example.org/a")
NOTES = EVIDENCE + "\n\n" + RECIPES


class FakeAgent:
    """Stands in for pantheon.agent.Agent: writes a scripted exchange into the memory it is given;
    the evidence agent answers with evidence notes, the recipe agent with recipe notes."""
    script = "answer"
    runs: List[dict] = []

    def __init__(self, name, instructions, model, model_params=None, use_memory=True,
                 tool_timeout=None, max_tool_content_length=None):
        self.name, self.model, self.model_params = name, model, model_params
        self.tools = []

    async def toolset(self, ts):
        self.tools.append(ts)

    async def run(self, msg, memory=None, max_turns=float("inf"), tool_use=True, **kw):
        FakeAgent.runs.append({"agent": self.name, "msg": msg, "tool_use": tool_use,
                               "max_turns": max_turns})
        call = {"role": "assistant", "content": "", "_metadata": {"total_tokens": 100, "current_cost": 0.01},
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "web_search", "arguments": "{}"}}]}
        result = {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"results": ["A leads"]})}
        text = EVIDENCE if self.name.endswith("evidence") else RECIPES
        answer = {"role": "assistant", "content": text,
                  "_metadata": {"total_tokens": 200, "current_cost": 0.02}}
        if FakeAgent.script == "answer":
            memory.add_messages([{"role": "user", "content": msg}, call, result, answer])
        elif FakeAgent.script == "turn-cap":
            if tool_use:
                memory.add_messages([{"role": "user", "content": msg}, call, result])
            else:
                memory.add_messages([{"role": "user", "content": msg}, answer])
        elif FakeAgent.script == "slow":
            memory.add_messages([{"role": "user", "content": msg}, call, result])
            await asyncio.sleep(30)
        elif FakeAgent.script == "fail":
            memory.add_messages([{"role": "user", "content": msg}, call, result])
            raise RuntimeError("model endpoint down")
        elif FakeAgent.script == "fail-once":
            if len(FakeAgent.runs) == 1:
                memory.add_messages([{"role": "user", "content": msg}, call, result])
                raise RuntimeError("JSON error injected into SSE stream")
            memory.add_messages([{"role": "user", "content": msg}, answer])


@pytest.fixture
def fake_agent(monkeypatch):
    import pantheon.agent

    FakeAgent.runs = []
    monkeypatch.setattr(pantheon.agent, "Agent", FakeAgent)
    return FakeAgent


def _run(**kw):
    return asyncio.run(R.run_research(model="openai/x/y", objective="maximise v", seed_text="v=1",
                                      **kw))


def test_evidence_then_recipes_are_joined(fake_agent):
    fake_agent.script = "answer"
    notes, log = _run(secs=30)
    assert notes == NOTES
    assert [r["agent"] for r in fake_agent.runs] == ["lab-notebook-evidence", "lab-notebook-recipes"]
    assert fake_agent.runs[0]["max_turns"] == 50
    recipe_prompt = fake_agent.runs[1]["msg"]
    assert "A first, B second" in recipe_prompt, "the recipe agent is given the evidence notes"
    assert "Number your sources from [2]" in recipe_prompt
    assert log["evidence"]["stopped"] == "answered" and log["recipes"]["stopped"] == "answered"
    assert log["calls"] == 4 and log["usd_est"] == pytest.approx(0.06)


def test_turn_cap_gets_one_tool_free_write_up(fake_agent):
    fake_agent.script = "turn-cap"
    notes, log = _run(secs=30, turns=6)
    assert notes == NOTES
    assert log["evidence"]["stopped"] == "turns" and log["evidence"]["finish"] == "agent"
    assert fake_agent.runs[-1]["tool_use"] is False and fake_agent.runs[-1]["max_turns"] == 1


def test_clock_then_fallback_write_up_from_the_material(fake_agent):
    fake_agent.script = "slow"
    seen = []

    async def fallback(system, prompt):
        seen.append(prompt)
        return EVIDENCE if "EVIDENCE ABOUT" in system else RECIPES

    notes, log = _run(secs=20.0, finish_secs=10.0, fallback_llm=fallback)
    assert log["evidence"]["stopped"] == "clock" and log["evidence"]["finish"] == "fallback"
    assert log["recipes"]["finish"] == "fallback"
    assert notes == NOTES
    assert "A leads" in seen[0], "the fallback writes from what the agent read"
    assert log["secs"] < 30


def test_a_failed_model_call_is_continued_on_the_same_conversation(fake_agent):
    fake_agent.script = "fail-once"
    notes, log = _run(secs=300, finish_secs=10)
    assert notes == NOTES and log["evidence"]["resumed"] == 1
    assert fake_agent.runs[1]["msg"].startswith("Continue the research")


def test_failure_without_fallback_means_no_notes(fake_agent):
    fake_agent.script = "fail"
    notes, log = _run(secs=8, finish_secs=2)
    assert notes == ""
    assert log["evidence"]["stopped"].startswith("error: RuntimeError")


def test_count_sources():
    assert R.count_sources(EVIDENCE) == 1
    assert R.count_sources("## Sources\n1. a\n2. b\n[3] c") == 3
    assert R.count_sources("no sources") == 0


def test_final_notes_needs_a_final_answer_with_the_headings():
    call = {"role": "assistant", "content": NOTES, "tool_calls": [{"id": "1"}]}
    assert R.final_notes([{"role": "assistant", "content": NOTES}]) == NOTES
    assert R.final_notes([{"role": "assistant", "content": NOTES}, call]) == ""
    assert R.final_notes([{"role": "assistant", "content": RECIPES}], R.EVIDENCE_HEADINGS) == ""
    assert R.final_notes([{"role": "assistant", "content": "I found things."}]) == ""


class _FakeWeb:
    async def web_search(self, query, max_results):
        return {"success": True, "results": [{"url": "https://good.org/a", "title": "a"},
                                             {"url": "https://github.com/Secret/bench", "title": "b"}]}

    async def web_fetch(self, url):
        return {"success": True, "content": "static page " + url + " " + "x" * 300}


class _FakeBrowser:
    def __init__(self, text=None, fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    async def web_crawl(self, urls, timeout=20.0):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no chromium")
        return [self.text]


class _FakePapers:
    async def paper_search(self, query, max_results):
        return {"success": True, "results": [{"url": "https://arxiv.org/abs/1", "title": "p"}]}


def _tools(browser):
    ts = R.ResearchToolSet("t", block=["secret/bench"], page_chars=1000)
    ts._web, ts._papers, ts._browser = _FakeWeb(), _FakePapers(), browser
    return ts


def test_block_list_hides_sources_and_everything_is_logged():
    ts = _tools(_FakeBrowser(text="rendered " * 100))
    r = asyncio.run(ts.web_search("dimensionality reduction benchmark"))
    assert [x["url"] for x in r["results"]] == ["https://good.org/a"]
    assert asyncio.run(ts.web_browse("https://github.com/Secret/bench/blob/x.py"))["success"] is False
    assert asyncio.run(ts.web_browse("https://good.org/a"))["text"].startswith("rendered")
    asyncio.run(ts.paper_search("t-SNE"))
    assert ts.log["web_queries"] == ["dimensionality reduction benchmark"]
    assert ts.log["paper_queries"] == ["t-SNE"]
    assert ts.log["fetched"] == ["https://good.org/a"]
    assert len(ts.log["blocked"]) == 2 and ts.log["rendered"] == 1
    assert set(ts.tool_functions) == {"web_search", "web_browse", "paper_search"}


def test_browse_pages_finds_and_caches_a_long_rendered_page():
    table = "intro " * 400 + "| densMAP (logCP10k) | 0.7021 | 0.8500 |" + " tail" * 400
    br = _FakeBrowser(text=table)
    ts = _tools(br)
    first = asyncio.run(ts.web_browse("https://lb.org/dr"))
    assert len(first["text"]) == 1000 and first["next_start"] == 1000 and first["length"] == len(table)
    hit = asyncio.run(ts.web_browse("https://lb.org/dr", find="DENSMAP"))
    assert hit["matches"] == 1 and "0.8500" in hit["passages"][0]
    last = asyncio.run(ts.web_browse("https://lb.org/dr", start=len(table) - 10))
    assert last["next_start"] is None
    assert br.calls == 1, "the page is rendered once and then served from the cache"


def test_browse_falls_back_to_a_static_fetch_without_a_browser():
    ts = _tools(_FakeBrowser(fail=True))
    r = asyncio.run(ts.web_browse("https://good.org/a"))
    assert r["success"] and r["text"].startswith("static page") and ts.log["static"] == 1


class PromptLLM:
    def __init__(self):
        self.prompts: List[tuple] = []

    async def __call__(self, system, prompt):
        self.prompts.append((system, prompt))
        if "proposing the next experiments" in system:
            return json.dumps([{"title": "idea", "idea": "Do it.", "predicted_gain": 0.0}])
        if "writing up one experiment" in system:
            return json.dumps({"outcome": "o", "why": "w", "adherence": 1, "lesson": "l"})
        return "## Problem structure\nnotebook\n"


class _Var:
    async def create(self, ctx, item):
        from pantheon.evolution.core import Produced
        return [Produced(genome=CodeGenome(files={"solution.py": "# value=1.0\n"}), item_id=item.id,
                         batch_id=item.batch_id, parent_ids=list(item.parent_ids),
                         anchor_id=item.anchor_id) for _ in range(item.k)]


class _Eval:
    kind = CODE

    async def measure(self, ctx, ind, fidelity="full"):
        from pantheon.evolution.core import Measurement
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": 1.0, "validity": 1.0}, cost=0.0)


def _evolve(m, llm):
    m._llm = llm  # type: ignore[assignment]
    return asyncio.run(evolve(method=m, variator=_Var(), evaluators={CODE: _Eval()},
                              seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
                              objective="maximise value", budget=Budget(max_items=2), concurrency=2))


def test_notes_and_prior_reach_understand_and_survive_a_checkpoint(monkeypatch, tmp_path):
    async def fake_research(**kw):
        assert kw["block"] == ["bench"] and kw["secs"] == 60
        return NOTES, {"stopped": "answered", "calls": 3, "tokens": 900, "usd_est": 0.05,
                       "secs": 12.0, "web_queries": ["q"], "paper_queries": [], "fetched": ["u"],
                       "blocked": []}

    monkeypatch.setattr(R, "run_research", fake_research)
    prior = tmp_path / "prior.md"
    prior.write_text("PRIOR TEXT")
    m = LabNotebook(ideas_per_cycle=1, steps_per_trajectory=1, k_candidates=1, research=True,
                    research_secs=60, research_block=["bench"], prior_file=str(prior))
    llm = PromptLLM()
    _evolve(m, llm)
    understand = next(p for s, p in llm.prompts if s.startswith("You are a research scientist keeping"))
    assert "# Literature notes" in understand and "use A with k=30" in understand
    assert "# Prior knowledge about this problem" in understand and "PRIOR TEXT" in understand
    ev = next(e for e in m.events if e["kind"] == "research")
    assert ev["calls"] == 3 and ev["web_queries"] == 1 and ev["fetched"] == 1
    und = next(e for e in m.events if e["kind"] == "understand")
    assert und["research_chars"] == len(NOTES) and und["prior_chars"] == len("PRIOR TEXT")
    m2 = LabNotebook()
    m2.load_state_dict(json.loads(json.dumps(m.state_dict())))
    assert m2.research_notes == NOTES and m2.research_log["calls"] == 3


def test_research_off_by_default_and_no_prior_leaves_the_prompt_unchanged(monkeypatch):
    monkeypatch.delenv("LABNOTEBOOK_PRIOR_FILE", raising=False)

    async def boom(**kw):
        raise AssertionError("research must not run by default")

    monkeypatch.setattr(R, "run_research", boom)
    m = LabNotebook(ideas_per_cycle=1, steps_per_trajectory=1, k_candidates=1)
    llm = PromptLLM()
    _evolve(m, llm)
    understand = next(p for s, p in llm.prompts if s.startswith("You are a research scientist keeping"))
    assert understand.endswith("Write the initial notebook.")
    assert "Literature notes" not in understand and "Prior knowledge" not in understand


def test_paper_search_falls_back_in_order_and_rebuilds_openalex_abstracts(monkeypatch):
    from pantheon.toolsets import paper_search as P

    def s2(*a, **k):
        raise RuntimeError("429")

    class _R:
        def json(self):
            return {"results": [{"title": "densMAP", "publication_year": 2021, "cited_by_count": 5,
                                 "doi": "https://doi.org/10.1/x",
                                 "primary_location": {"source": {"display_name": "Nat Biotech"}},
                                 "abstract_inverted_index": {"density": [1], "Local": [0], "matters": [2]}}]}

    monkeypatch.setattr(P, "search_semantic_scholar", s2)
    monkeypatch.setattr(P, "_get", lambda *a, **k: _R())
    r = asyncio.run(P.PaperSearchToolSet("t").paper_search("densmap", 3))
    assert r["success"] and r["source"] == "openalex"
    assert r["results"][0]["abstract"] == "Local density matters"
    assert r["results"][0]["venue"] == "Nat Biotech"
