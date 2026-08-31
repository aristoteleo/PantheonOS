"""The upstream-style generation prompt matches the SimpleTES authors' template.

Shape-pinned against `simpletes/templates/generation.py` at commit a19a54b1: their section
headers, their rule list with the language named, inspirations as full programs with full
metric dicts, and no system message on the wire.
"""
from __future__ import annotations

from types import SimpleNamespace

from pantheon.evolution.core import CodeGenome, Individual, Measurement
from pantheon.evolution.variators.completion import CompletionVariator, EvolveBlock

SEED = ("#include <bits/stdc++.h>\n"
        "// EVOLVE-BLOCK-START\n"
        "int core() { return 1; }\n"
        "// EVOLVE-BLOCK-END\n"
        "int main() { return core(); }\n")


def _parent(score: float) -> Individual:
    ind = Individual(genome=CodeGenome(files={"solution.cpp": SEED}))
    ind.measurements.append(Measurement(
        individual_id=ind.id, ok=True, fidelity="full",
        metrics={"combined_score": score, "raw_score": score * 1500, "validity": 1.0}))
    return ind


def _prompt(v: CompletionVariator, parents, failures=None) -> str:
    item = SimpleNamespace(context=SimpleNamespace(
        instruction="Catch fish.", parents=parents, history="", inspirations=[],
        failures=failures or {}))
    ctx = SimpleNamespace(objective="Catch fish.")
    return v.build_block_prompt(ctx, item, "solution.cpp", EvolveBlock(SEED))


def test_upstream_template_shape():
    v = CompletionVariator(model="m", target_file="solution.cpp", upstream_style=True)
    text = _prompt(v, [_parent(2.4), _parent(2.5)], failures={"timeout": 3})
    assert text.startswith("Task: Catch fish.")
    assert "4) Return one C++ code block that includes both EVOLVE-BLOCK markers." in text
    assert "EXACT_PREFIX (kept unchanged):\n```cpp\n" in text
    assert "=== REFERENCE SOLUTIONS ===" in text
    assert "[SAMPLED INSPIRATIONS] (2 solutions sampled for detailed reference)" in text
    assert "--- Inspiration 1 ---" in text
    assert "raw_score: 3750.000000" in text          # full metrics dict, floats at 6dp
    assert text.index("Score: 2.5") < text.index("Score: 2.4")   # sorted by score, best first
    assert text.count("int main() { return core(); }") >= 3      # prefix/suffix AND full-program inspirations
    assert "[FAILURE PATTERNS] (common errors to avoid)" in text
    assert "- Avoid the listed failure patterns" in text
    assert text.rstrip().endswith("Generate an improved solution with higher score:")


def test_upstream_style_sends_no_system_message():
    v = CompletionVariator(model="m", upstream_style=True)
    assert v.system_prompt == ""
    explicit = CompletionVariator(model="m", system_prompt="be brief")
    assert explicit.system_prompt == "be brief"


def test_default_style_unchanged():
    v = CompletionVariator(model="m", target_file="solution.cpp")
    text = _prompt(v, [_parent(2.4)])
    assert not text.startswith("Task:")
    assert v.system_prompt != ""


def test_simpletes_owns_the_upstream_prompt():
    from pantheon.evolution.methods import SimpleTES

    op = SimpleTES(seed=0).default_variator(model="m", target_file="solution.cpp")
    assert op.upstream_style is True
    assert op.system_prompt == ""
    assert op.max_tokens == 32768
