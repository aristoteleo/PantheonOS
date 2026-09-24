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

NOTES = ("## How the score is built\nrank correlation [1]\n## What published benchmarks show\n"
         "method A leads [1]\n## Method recipes\nuse A with k=30 [1]\n## Engineering\nfast\n"
         "## Sources\n[1] A paper, https://example.org/a")


class FakeAgent:
    """Stands in for pantheon.agent.Agent: writes a scripted exchange into the memory it is given."""
    script = "answer"
    runs: List[dict] = []

    def __init__(self, name, instructions, model, model_params=None, use_memory=True,
                 tool_timeout=None, max_tool_content_length=None):
        self.model, self.model_params = model, model_params
        self.tools = []

    async def toolset(self, ts):
        self.tools.append(ts)

    async def run(self, msg, memory=None, max_turns=float("inf"), tool_use=True, **kw):
        FakeAgent.runs.append({"msg": msg, "tool_use": tool_use, "max_turns": max_turns})
        call = {"role": "assistant", "content": "", "_metadata": {"total_tokens": 100, "current_cost": 0.01},
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "web_search", "arguments": "{}"}}]}
        result = {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"results": ["A leads"]})}
        answer = {"role": "assistant", "content": NOTES,
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


@pytest.fixture
def fake_agent(monkeypatch):
    import pantheon.agent

    FakeAgent.runs = []
    monkeypatch.setattr(pantheon.agent, "Agent", FakeAgent)
    return FakeAgent


def _run(**kw):
    return asyncio.run(R.run_research(model="openai/x/y", objective="maximise v", seed_text="v=1",
                                      **kw))


def test_agent_answer_is_the_notes(fake_agent):
    fake_agent.script = "answer"
    notes, log = _run(secs=30)
    assert notes == NOTES
    assert log["stopped"] == "answered" and not log["finish"]
    assert log["calls"] == 2 and log["usd_est"] == pytest.approx(0.03)
    assert len(fake_agent.runs) == 1 and fake_agent.runs[0]["max_turns"] == 40


def test_turn_cap_gets_one_tool_free_write_up(fake_agent):
    fake_agent.script = "turn-cap"
    notes, log = _run(secs=30, turns=3)
    assert notes == NOTES
    assert log["stopped"] == "turns" and log["finish"] == "agent"
    assert fake_agent.runs[-1]["tool_use"] is False and fake_agent.runs[-1]["max_turns"] == 1


def test_clock_then_fallback_write_up_from_the_material(fake_agent):
    fake_agent.script = "slow"
    seen = {}

    async def fallback(system, prompt):
        seen["prompt"] = prompt
        return NOTES

    notes, log = _run(secs=8.0, finish_secs=5.0, fallback_llm=fallback)
    assert log["stopped"] == "clock"
    assert notes == NOTES and log["finish"] == "fallback"
    assert "A leads" in seen["prompt"], "the fallback writes from what the agent read"
    assert log["secs"] < 12


def test_failure_without_fallback_means_no_notes(fake_agent):
    fake_agent.script = "fail"
    notes, log = _run(secs=5, finish_secs=1)
    assert notes == ""
    assert log["stopped"].startswith("error: RuntimeError")


def test_final_notes_needs_a_final_answer_with_the_headings():
    call = {"role": "assistant", "content": NOTES, "tool_calls": [{"id": "1"}]}
    assert R.final_notes([{"role": "assistant", "content": NOTES}]) == NOTES
    assert R.final_notes([{"role": "assistant", "content": NOTES}, call]) == ""
    assert R.final_notes([{"role": "assistant", "content": "I found things."}]) == ""


class _FakeWeb:
    async def web_search(self, query, max_results):
        return {"success": True, "results": [{"url": "https://good.org/a", "title": "a"},
                                             {"url": "https://github.com/Secret/bench", "title": "b"}]}

    async def web_fetch(self, url):
        return {"success": True, "content": "page " + url}


class _FakePapers:
    async def paper_search(self, query, max_results):
        return {"success": True, "results": [{"url": "https://arxiv.org/abs/1", "title": "p"}]}


def test_block_list_hides_sources_and_everything_is_logged():
    ts = R.ResearchToolSet("t", block=["secret/bench"])
    ts._web, ts._papers = _FakeWeb(), _FakePapers()
    r = asyncio.run(ts.web_search("dimensionality reduction benchmark"))
    assert [x["url"] for x in r["results"]] == ["https://good.org/a"]
    assert asyncio.run(ts.web_fetch("https://github.com/Secret/bench/blob/x.py"))["success"] is False
    assert asyncio.run(ts.web_fetch("https://good.org/a"))["content"] == "page https://good.org/a"
    asyncio.run(ts.paper_search("t-SNE"))
    assert ts.log["web_queries"] == ["dimensionality reduction benchmark"]
    assert ts.log["paper_queries"] == ["t-SNE"]
    assert ts.log["fetched"] == ["https://good.org/a"]
    assert len(ts.log["blocked"]) == 2
    assert set(ts.tool_functions) == {"web_search", "web_fetch", "paper_search"}


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
