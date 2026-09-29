"""The subprocess evaluator must read its result past whatever the program printed.

A program that prints (SimpleTES's autocorrelation seed does; CodeEvolve's hexagon verifier
does) used to turn its own measurement into "No output from evaluator": the result JSON was
parsed from the whole stdout stream.
"""
import asyncio

import pytest

from pantheon.evolution.evaluator import HybridEvaluator

CHATTY = '''
def evaluate(workspace_path):
    print("This gets a C2 lower bound of 0.91 {not json")
    print("no trailing newline", end="")
    return {"combined_score": 0.5, "validity": 1.0}
'''

RAISING = '''
def evaluate(workspace_path):
    print("about to fail")
    raise ValueError("boom")
'''


def _run(code, tmp_path):
    ev = HybridEvaluator(code, feedback_agent=None, timeout=60)
    return asyncio.run(ev._run_with_subprocess(str(tmp_path)))


def test_result_survives_program_stdout(tmp_path):
    r = _run(CHATTY, tmp_path)
    assert "error" not in r
    assert r["combined_score"] == 0.5 and r["validity"] == 1.0


def test_error_still_reported_after_program_stdout(tmp_path):
    r = _run(RAISING, tmp_path)
    assert r["function_score"] == 0.0
    assert "boom" in r["error"]
