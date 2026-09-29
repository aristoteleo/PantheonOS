"""The operator's own feasibility gate.

`success` from an evaluator means it ran without crashing. An invalid solution also manages that:
it comes back `success=True, validity=0, combined_score=0`. Three places in `AgentVariator` read
`success` and accepted a broken program -- the best-so-far tracker, the on-disk salvage, and
`submit`, which checked nothing at all and simply snapshotted the working directory.

Measured across eight Erdos runs before the fix: 38 of 213 committed programs violated the
constraints, and 22 of those 38 came from agents that HAD called `run_evaluator` -- they verified
one version, edited again, and submitted the edit unchecked. The agent has a ReAct loop and a tool
that reports exactly why a solution is invalid; what it did not have was anything that made it
look before committing.

These tests pin the gate, not the agent. No model is called.
"""
from __future__ import annotations

import pytest

from pantheon.evolution.variators.agent import AgentVariator, _Session

VALID = {"success": True, "metrics": {"validity": 1.0, "combined_score": 0.6}}
BETTER = {"success": True, "metrics": {"validity": 1.0, "combined_score": 0.9}}
INVALID = {"success": True, "metrics": {"validity": 0.0, "combined_score": 0.0},
           "artifacts": {"feedback": "sum(h) != K/2"}}
CRASHED = {"success": False, "error": "boom", "metrics": {}}


def sess(tmp_path) -> _Session:
    (tmp_path / "main.py").write_text("seed\n")
    return _Session(workdir=tmp_path, parent_files={"main.py": "seed\n"})


def test_feasible_separates_validity_from_success():
    v = AgentVariator(evaluator=None)
    assert v._feasible(VALID)[0] is True
    assert v._feasible(INVALID)[0] is False, "success=True is not the same as valid"
    assert v._feasible(CRASHED)[0] is False
    assert "sum(h)" in v._feasible(INVALID)[1], "the reason has to reach the agent"


def test_an_evaluator_that_reports_no_validity_is_taken_at_its_word():
    """Not every problem has constraints. Absence of the metric must mean 'fine', not 'invalid',
    or this gate would reject every program on such a problem."""
    v = AgentVariator(evaluator=None)
    assert v._feasible({"success": True, "metrics": {"combined_score": 0.6}})[0] is True


def test_best_only_remembers_feasible_versions(tmp_path):
    v = AgentVariator(evaluator=None)
    s = sess(tmp_path)
    v._remember_best(s, {"main.py": "a"}, INVALID)
    assert s.best is None, "an invalid program must never become the fallback"
    v._remember_best(s, {"main.py": "b"}, VALID)
    assert s.best["score"] == pytest.approx(0.6)
    v._remember_best(s, {"main.py": "c"}, BETTER)
    assert s.best["files"]["main.py"] == "c"
    v._remember_best(s, {"main.py": "d"}, VALID)
    assert s.best["files"]["main.py"] == "c", "a worse valid version does not displace the best"


def test_crashed_evaluation_never_becomes_the_fallback(tmp_path):
    v = AgentVariator(evaluator=None)
    s = sess(tmp_path)
    v._remember_best(s, {"main.py": "a"}, CRASHED)
    assert s.best is None


def test_valid_key_is_configurable():
    v = AgentVariator(evaluator=None, valid_key="feasible")
    assert v._feasible({"success": True, "metrics": {"feasible": 0.0}})[0] is False
    assert v._feasible({"success": True, "metrics": {"validity": 0.0}})[0] is True, \
        "the default name must not be consulted once another one is chosen"
