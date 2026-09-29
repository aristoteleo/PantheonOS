"""Fidelity must reach the evaluator, not just the Measurement label.

`ProgramEvaluatorAdapter.measure` once recorded `fidelity="low"` while calling
`evaluate_files(files)` without it -- no `.fidelity` marker, so every "cheap screen" a method
issued ran at full price. The wave4 HypothesisBandit runs paid full for all three of their
screens because of exactly this line.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pantheon.evolution.core import Individual, TextGenome
from pantheon.evolution.variators.adapters import ProgramEvaluatorAdapter


class _RecordingInner:
    """Captures the snapshot each evaluate() call receives."""

    def __init__(self):
        self.payloads = []

    async def evaluate(self, program):
        self.payloads.append(dict(program.snapshot.files))
        return SimpleNamespace(success=True, metrics={"combined_score": 1.0}, artifacts={},
                               error=None, state=None)


def _adapter():
    inner = _RecordingInner()
    a = ProgramEvaluatorAdapter(inner)
    return a, inner


def _ind():
    return Individual(genome=TextGenome(text="x = 1", kind="code"))


def test_measure_threads_fidelity_to_the_marker():
    a, inner = _adapter()
    asyncio.run(a.measure(SimpleNamespace(), _ind(), fidelity="low"))
    assert inner.payloads[0].get(ProgramEvaluatorAdapter.FIDELITY_MARKER) == "low"


def test_full_fidelity_writes_no_marker():
    a, inner = _adapter()
    asyncio.run(a.measure(SimpleNamespace(), _ind(), fidelity="full"))
    assert ProgramEvaluatorAdapter.FIDELITY_MARKER not in inner.payloads[0]


def test_evaluate_books_the_eval_ledger():
    from pantheon.evolution.variators import usage

    a, _ = _adapter()
    before = usage.eval_snapshot()
    asyncio.run(a.measure(SimpleNamespace(), _ind(), fidelity="low"))
    asyncio.run(a.measure(SimpleNamespace(), _ind(), fidelity="full"))
    after = usage.eval_snapshot()
    assert after["calls"] == before["calls"] + 2
    assert after["calls_low"] == before["calls_low"] + 1
