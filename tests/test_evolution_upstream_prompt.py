"""SimpleTES's own operator speaks the authors' template.

Shape-pinned against `simpletes/templates/generation.py` at commit a19a54b1. The prompt lives
in `UpstreamCompletionVariator` -- a method-owned subclass, per the design rule that a method
implements its OWN variator and inherits only machinery; the base `CompletionVariator` knows
nothing about upstream.
"""
from __future__ import annotations

from types import SimpleNamespace

from pantheon.evolution.core import CodeGenome, Individual, Measurement
from pantheon.evolution.methods.simpletes import SimpleTES, UpstreamCompletionVariator
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
    v = UpstreamCompletionVariator(model="m", target_file="solution.cpp")
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


def test_upstream_subclass_sends_no_system_message():
    assert UpstreamCompletionVariator.SYSTEM == ""
    assert UpstreamCompletionVariator(model="m").system_prompt == ""
    assert CompletionVariator(model="m").system_prompt != ""      # base untouched
    assert CompletionVariator(model="m", system_prompt="be brief").system_prompt == "be brief"


def test_base_has_no_upstream_knowledge():
    v = CompletionVariator(model="m", target_file="solution.cpp")
    text = _prompt(v, [_parent(2.4)])
    assert not text.startswith("Task:")
    assert not hasattr(v, "upstream_style")


def test_simpletes_owns_the_upstream_prompt():
    op = SimpleTES(seed=0).default_variator(model="m", target_file="solution.cpp")
    assert type(op) is UpstreamCompletionVariator
    assert op.system_prompt == ""
    assert op.max_tokens == 32768


def test_methods_own_their_agent_words():
    from pantheon.evolution.methods.annealed_idea_code import ImplementAgentVariator
    from pantheon.evolution.methods.hypothesis_bandit import EditAgentVariator
    from pantheon.evolution.variators.agent import AgentVariator

    assert "EDITING" in EditAgentVariator.SYSTEM
    assert "approach you were given" in ImplementAgentVariator.SYSTEM
    assert EditAgentVariator.SYSTEM != AgentVariator.SYSTEM
    assert ImplementAgentVariator.SYSTEM != AgentVariator.SYSTEM
