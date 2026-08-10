"""Asking for a cheaper measurement, without letting a cheap number become the recorded one.

AHC039 scores 150 test cases three times over: 124 seconds. An agent that calls `run_evaluator`
four times while exploring spends eight minutes per mutation on measurement alone, and a smoke run
came out at fifteen minutes an item. A 30-case reading answers "did that help?" in ten seconds and
lands within 0.4% of the full score, so the agent's own loop can use it.

What must not happen is that reading being recorded. `submit` and the salvage path decide what
becomes a child, so both measure at full fidelity whatever the agent was using.

The request travels as a file in the workspace rather than an environment variable, because
several mutations evaluate concurrently in one process and a shared setting would let one agent's
cheap reading be recorded as another's score.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from pantheon.evolution.variators.adapters import ProgramEvaluatorAdapter
from pantheon.evolution.variators.agent import AgentVariator, _Session


class RecordingInner:
    """Stands in for HybridEvaluator: remembers the files of every evaluation it is asked for."""

    def __init__(self):
        self.seen: List[Dict[str, str]] = []

    async def evaluate(self, program):
        self.seen.append(dict(program.snapshot.files))
        return type("R", (), {"success": True, "metrics": {"combined_score": 1.0,
                                                           "validity": 1.0},
                              "artifacts": {}, "error": None, "state": None})()


def test_full_fidelity_leaves_the_workspace_untouched():
    inner = RecordingInner()
    ad = ProgramEvaluatorAdapter(inner)
    asyncio.run(ad.evaluate_files({"solution.py": "x"}))
    assert ad.FIDELITY_MARKER not in inner.seen[0], (
        "the default must add nothing; every existing task evaluator ignores the marker and "
        "would otherwise see a file it did not put there")


def test_a_cheaper_fidelity_is_requested_through_the_workspace():
    inner = RecordingInner()
    ad = ProgramEvaluatorAdapter(inner)
    asyncio.run(ad.evaluate_files({"solution.py": "x"}, fidelity="low"))
    assert inner.seen[0][ad.FIDELITY_MARKER] == "low"
    assert inner.seen[0]["solution.py"] == "x", "the program itself must be unchanged"


class FidelityEvaluator:
    """Records the fidelity of each call, the way a two-fidelity task evaluator would see it."""

    def __init__(self):
        self.calls: List[str] = []

    async def evaluate_files(self, files: Dict[str, str], fidelity: str = "full") -> Dict[str, Any]:
        self.calls.append(fidelity)
        return {"success": True, "metrics": {"combined_score": 1.0, "validity": 1.0},
                "artifacts": {}, "error": None}


def _sess(tmp_path):
    (tmp_path / "solution.py").write_text("x")
    return _Session(workdir=tmp_path, parent_files={"solution.py": "x"})


def test_submit_measures_at_full_fidelity_even_when_the_agent_explores_cheaply(tmp_path):
    ev = FidelityEvaluator()
    v = AgentVariator(evaluator=ev, inner_fidelity="low")
    s = _sess(tmp_path)
    asyncio.run(v._evaluate(s.current_files(), v.inner_fidelity))     # exploratory
    asyncio.run(v._evaluate(s.current_files(), "full"))               # what submit does
    assert ev.calls == ["low", "full"], (
        "a cheap reading must never be the one that decides what is recorded")


def test_an_evaluator_without_fidelities_still_works(tmp_path):
    class Old:
        def __init__(self):
            self.n = 0

        async def evaluate_files(self, files):     # no fidelity parameter at all
            self.n += 1
            return {"success": True, "metrics": {}, "artifacts": {}, "error": None}

    old = Old()
    v = AgentVariator(evaluator=old, inner_fidelity="low")
    out = asyncio.run(v._evaluate({"solution.py": "x"}, "low"))
    assert old.n == 1 and out["success"] is True


def test_inner_fidelity_defaults_to_full():
    assert AgentVariator(evaluator=None).inner_fidelity == "full", (
        "a task with one fidelity must not silently start measuring something else"
    )
