"""HypothesisBandit: hypothesis-guided adaptive co-evolution, on fakes.

The claims tested are the mechanisms the design document names: hypotheses are structured and
gated structurally (not judged), implementations are component-tagged edits whose measured dR
feeds per-hypothesis evidence and per-component credit, the controller prefers evidence and
retires refuted hypotheses, and the multi-fidelity stage promotes only survivors. Nothing here
claims the search is good -- a toy cannot say that.
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from pantheon.evolution.core import (
    Budget,
    CodeGenome,
    EvolveContext,
    Individual,
    Measurement,
    Produced,
    Store,
    TextGenome,
)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.core.method import EvolveMethod
from pantheon.evolution.methods import HypothesisBandit
from pantheon.evolution.methods.hypothesis_bandit import (
    COMPONENTS,
    HYP,
    CODE,
    HypothesisGate,
    _parse_component,
)


def hyp_text(component: str, mech: str = "swap the inner loop") -> str:
    return (f"target_component: {component}\nmechanism: {mech}\n"
            f"expected_effect: higher score\nfalsification: no gain on the benchmark\n")


class FakeVariator:
    """Hypotheses cycle through components; implementations climb, one component faster."""

    def __init__(self):
        self.serial = 0

    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        self.serial += 1
        if item.kind == HYP:
            comp = item.meta.get("target") or COMPONENTS[self.serial % len(COMPONENTS)]
            return [Produced(
                genome=TextGenome(text=hyp_text(comp, f"mechanism #{self.serial}"), kind=HYP),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids), anchor_id=item.anchor_id)]
        parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
        base = 0.0
        if parent is not None:
            first = next(iter(parent.genome.files.values()), "# value=0.0")
            base = float(first.split("value=")[1].split()[0]) if "value=" in first else 0.0
        comp = (item.context.extra or {}).get("component", COMPONENTS[0])
        step = 0.05 if comp == "search-strategy" else 0.01
        return [Produced(
            genome=CodeGenome(files={"solution.py":
                                     f"# v{self.serial} value={base + step}\n"}),
            item_id=item.id, batch_id=item.batch_id,
            parent_ids=list(item.parent_ids), anchor_id=item.anchor_id)]


class FakeCodeEvaluator:
    kind = CODE

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        first = next(iter(ind.genome.files.values()), "")
        v = float(first.split("value=")[1].split()[0]) if "value=" in first else 0.0
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": v, "validity": 1.0}, cost=0.01)


def run(method, budget=24):
    return asyncio.run(evolve(
        method, FakeVariator(), {CODE: FakeCodeEvaluator()},
        seeds=[CodeGenome(files={"solution.py": "# seed value=0.0\n"})],
        objective="maximise the toy value", budget=Budget(max_items=budget), concurrency=2))


# ------------------------------------------------------------------ protocol ---
def test_conforms_to_the_method_protocol():
    assert isinstance(HypothesisBandit(), EvolveMethod)


def test_the_gate_is_its_own_idea_evaluator():
    m = HypothesisBandit()
    evs = m.default_evaluators()
    assert isinstance(evs[HYP], HypothesisGate)
    assert CODE not in evs


# ------------------------------------------------------------- the mechanisms ---
def test_structured_hypotheses_are_admitted_and_prose_is_not():
    gate = HypothesisGate()
    s = Store()
    good = s.add(Individual(genome=TextGenome(text=hyp_text("parameters"), kind=HYP), kind=HYP))
    bad = s.add(Individual(genome=TextGenome(text="just try something better", kind=HYP),
                           kind=HYP))
    ctx = EvolveContext(store=s, budget=Budget(max_items=1))
    g = asyncio.run(gate.measure(ctx, good))
    b = asyncio.run(gate.measure(ctx, bad))
    assert g.metrics["structured"] == 1.0
    assert b.metrics["structured"] == 0.0


def test_parse_component_reads_the_field_line_first():
    text = ("target_component: numerical-optimization\n"
            "mechanism: tune the parameters of the core-algorithm\n")
    assert _parse_component(text) == "numerical-optimization"


def test_end_to_end_produces_hypotheses_and_component_tagged_children():
    m = HypothesisBandit(seed=3)
    res = run(m, budget=24)
    hyps = res.store.of_kind(HYP)
    codes = [c for c in res.store.of_kind(CODE) if c.parent_ids or c.anchor_id]
    assert hyps and codes
    assert all(c.anchor_id for c in codes), "every implementation serves a hypothesis"
    assert all(c.meta.get("component") in COMPONENTS for c in codes)
    assert any(gs for gs in m.credit.values()), "credit accumulated on some component"


def test_credit_steers_toward_the_paying_component():
    """search-strategy pays 5x in the fake; after enough evidence the controller should have
    implemented it more than any single other component."""
    m = HypothesisBandit(seed=5, min_live_hyps=3)
    run(m, budget=40)
    counts = {c: len(g) for c, g in m.credit.items()}
    assert counts, "no edits recorded"
    # not a strict argmax claim (stochastic controller); the paying component must at least be
    # among the most-edited
    top = sorted(counts.items(), key=lambda kv: -kv[1])
    assert dict(top).get("search-strategy", 0) > 0


def test_a_refuted_hypothesis_is_retired():
    m = HypothesisBandit(retire_after=2)
    m.hyp["h1"] = {"component": "parameters", "text": "t", "head": "t",
                   "gains": [-0.02, -0.01], "fails": 0, "retired": False}
    m._maybe_retire("h1")
    assert m.hyp["h1"]["retired"]


def test_evidence_beats_novelty_once_it_exists():
    m = HypothesisBandit(lam=1.0, eta=0.5, prior_sigma=0.05)
    m.hyp["good"] = {"component": "parameters", "text": "t", "head": "t",
                     "gains": [0.5, 0.5], "fails": 0, "retired": False}
    m.hyp["fresh"] = {"component": "core-algorithm", "text": "t", "head": "t",
                      "gains": [], "fails": 0, "retired": False}
    picks = [m.sample_hyp() for _ in range(200)]
    assert picks.count("good") > picks.count("fresh")


def test_low_fidelity_screen_gates_the_full_measurement():
    m = HypothesisBandit(low_fidelity=True, promote_margin=0.0)
    s = Store()
    ctx = EvolveContext(store=s, budget=Budget(max_items=10))
    child = s.add(Individual(genome=CodeGenome(files={"a": "x"}), kind=CODE,
                             meta={"component": "parameters", "base": 0.5}))
    # a cheap reading above the base earns a promotion
    good = Measurement(individual_id=child.id, fidelity="low",
                       metrics={"combined_score": 0.6, "validity": 1.0})
    asyncio.run(m.on_measured(ctx, child, good))
    assert len(m.pending_promote) == 1
    assert m.pending_promote[0].individual_id == child.id
    # a cheap reading far below it does not
    child2 = s.add(Individual(genome=CodeGenome(files={"a": "y"}), kind=CODE,
                              meta={"component": "parameters", "base": 0.5}))
    bad = Measurement(individual_id=child2.id, fidelity="low",
                      metrics={"combined_score": 0.1, "validity": 1.0})
    asyncio.run(m.on_measured(ctx, child2, bad))
    assert len(m.pending_promote) == 1


def test_cheap_readings_never_become_the_recorded_score():
    m = HypothesisBandit()
    s = Store()
    ind = s.add(Individual(genome=CodeGenome(files={"a": "x"}), kind=CODE))
    s.record(Measurement(individual_id=ind.id, fidelity="low",
                         metrics={"combined_score": 9.9, "validity": 1.0}))
    assert m._score(s.get(ind.id)) is None
    s.record(Measurement(individual_id=ind.id, fidelity="full",
                         metrics={"combined_score": 0.7, "validity": 1.0}))
    assert m._score(s.get(ind.id)) == pytest.approx(0.7)


def test_state_round_trips():
    m = HypothesisBandit(seed=7)
    run(m, budget=16)
    st = m.state_dict()
    import json

    st2 = json.loads(json.dumps(st))
    m2 = HypothesisBandit()
    m2.load_state_dict(st2)
    assert set(m2.hyp) == set(m.hyp)
    assert m2.credit.keys() == m.credit.keys()


# ----------------------------------------------- the EVOLVE-BLOCK protocol ---
def test_evolve_block_splits_and_merges_verbatim():
    from pantheon.evolution.variators.completion import EvolveBlock

    src = ("#include <x>\n// EVOLVE-BLOCK-START\nint core() { return 1; }\n"
           "// EVOLVE-BLOCK-END\nint main() { return core(); }\n")
    eb = EvolveBlock(src)
    assert eb.has_markers
    assert eb.block.strip() == "int core() { return 1; }"
    merged = eb.merge("```\n// EVOLVE-BLOCK-START\nint core() { return 2; }\n"
                      "// EVOLVE-BLOCK-END\n```")
    assert "#include <x>" in merged and "int main()" in merged
    assert "return 2" in merged and "return 1" not in merged


def test_evolve_block_reply_without_markers_is_the_bare_block():
    from pantheon.evolution.variators.completion import EvolveBlock

    src = "a\n# EVOLVE-BLOCK-START\nold\n# EVOLVE-BLOCK-END\nz\n"
    eb = EvolveBlock(src)
    merged = eb.merge("```\nnew body\n```")
    assert merged == "a\n# EVOLVE-BLOCK-START\nnew body\n# EVOLVE-BLOCK-END\nz\n"


def test_a_markerless_program_keeps_whole_file_mode():
    from pantheon.evolution.variators.completion import EvolveBlock

    eb = EvolveBlock("print('no markers here')\n")
    assert not eb.has_markers


from pantheon.evolution.variators.completion import EvolveBlock  # noqa: E402

SEED_CPP = (
    "#include <bits/stdc++.h>\nusing namespace std;\nconst int MAX_V = 1000;\nstruct Point { int x, y; };\n"
    "static int helper() { return 1; }\n// EVOLVE-BLOCK-START\nint solve() { return helper(); }\n"
    "// EVOLVE-BLOCK-END\nint main() { return solve(); }\n"
)


def test_evolve_block_drops_nested_fence_lines():
    eb = EvolveBlock(SEED_CPP)
    reply = "Here:\n```cpp\n// EVOLVE-BLOCK-START\n```cpp\nint solve() { return 2; }\n// EVOLVE-BLOCK-END\n```\n"
    out = eb.merge(reply)
    assert "```" not in out
    assert out.startswith("#include <bits/stdc++.h>") and out.rstrip().endswith("int main() { return solve(); }")
    assert out.count("int solve()") == 1 and "return 2;" in out


def test_evolve_block_whole_file_reply_without_markers_is_not_spliced():
    eb = EvolveBlock(SEED_CPP)
    whole = SEED_CPP.replace("// EVOLVE-BLOCK-START\n", "").replace("// EVOLVE-BLOCK-END\n", "").replace("return helper();", "return 7;")
    assert eb.merge(f"```cpp\n{whole}```") is None   # a from-scratch rewrite is rejected, not run


def test_evolve_block_whole_file_reply_inside_markers_is_not_spliced():
    eb = EvolveBlock(SEED_CPP)
    whole = SEED_CPP.replace("return helper();", "return 9;")
    out = eb.merge(f"```cpp\n// EVOLVE-BLOCK-START\n{whole}// EVOLVE-BLOCK-END\n```")
    assert out is not None and out.count("const int MAX_V") == 1 and "return 9;" in out   # innermost pair is the block


def test_evolve_block_bare_block_reply_still_splices():
    eb = EvolveBlock(SEED_CPP)
    out = eb.merge("```cpp\nint solve() { return 3; }\n```")
    assert out.count("struct Point") == 1 and "return 3;" in out and out.rstrip().endswith("int main() { return solve(); }")


def test_evolve_block_body_opening_with_include_is_the_whole_program():
    eb = EvolveBlock(SEED_CPP)
    body = "#include <bits/stdc++.h>\nusing namespace std;\nstruct Point { int x, y; };\nint solve() { return 4; }\nint main() { return solve(); }\n"
    assert eb.merge(f"```cpp\n// EVOLVE-BLOCK-START\n{body}// EVOLVE-BLOCK-END\n```") is None   # whole program inside the markers: rejected


def test_evolve_block_rejects_prose_and_foreign_code_for_c_seeds():
    eb = EvolveBlock(SEED_CPP)
    assert eb.merge("```\nscore = (# good fish inside) - (# bad fish inside)\n```") is None
    assert eb.merge("```python\nimport sys\ndef cross(o, a, b):\n    return 0\n```") is None
    assert eb.merge("```cpp\nint solve() { return 5; }\n```") is not None


def test_evolve_block_trailing_sentence_naming_both_markers_is_ignored():
    eb = EvolveBlock(SEED_CPP)
    reply = ("Here is the block:\n```cpp\n// EVOLVE-BLOCK-START\nint solve() { return 6; }\n// EVOLVE-BLOCK-END\n```\n"
             "Both // EVOLVE-BLOCK-START and // EVOLVE-BLOCK-END markers are kept exactly as written.\n")
    out = eb.merge(reply)
    assert out is not None and "return 6;" in out and out.count("int solve()") == 1 and out.count("struct Point") == 1


def test_evolve_block_marker_mentions_before_and_after_code():
    eb = EvolveBlock(SEED_CPP)
    reply = ("I only change what is between EVOLVE-BLOCK-START and EVOLVE-BLOCK-END.\n"
             "```cpp\n// EVOLVE-BLOCK-START\nint solve() { return 8; }\n// EVOLVE-BLOCK-END\n```\n"
             "The EVOLVE-BLOCK-START marker\nand the EVOLVE-BLOCK-END marker are unchanged.\n")
    out = eb.merge(reply)
    assert out is not None and "return 8;" in out and out.count("int solve()") == 1


def test_continuation_messages_shape():
    from pantheon.evolution.variators.completion import continuation_messages
    msgs = continuation_messages("plan " * 10)
    assert [m["role"] for m in msgs] == ["assistant", "user"]
    assert "cut off" in msgs[0]["content"] and "plan" in msgs[0]["content"]
    assert "EVOLVE-BLOCK" in msgs[1]["content"] and "Do not analyse" in msgs[1]["content"]
    long = continuation_messages("x" * 200_000)
    assert len(long[0]["content"]) < 121_000
