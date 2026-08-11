"""Where this port has to match SimpleTES, and where it deliberately does not.

Read against github.com/wq-will/SimpleTES. The first version of the port invented a distinction
that is not in the original -- a single "parent" plus decorative "inspirations" -- and that changed
what the algorithm is: a mutation of one program instead of a synthesis from several. These tests
pin the parts that were corrected, each against the upstream behaviour it mirrors.

Not ported, and not tested here: `reflection_mode` and the `llm_elite` / `llm_refine_*` selectors.
All three add a model call to the SELECTION step; the three selectors here decide with arithmetic.
"""
from __future__ import annotations

import asyncio
import random

import pytest

from pantheon.evolution.core import (Budget, EvolveContext, Individual, Measurement, Store,
                                     TextGenome)
from pantheon.evolution.methods.simpletes import (PUCTSelector, RPUCGSelector, Selector, SimpleTES)


def node(store, score, ident=""):
    ind = store.add(Individual(genome=TextGenome(text=f"prog {ident or score}", kind="code"),
                               kind="code"))
    ind.measurements.append(Measurement(individual_id=ind.id,
                                        metrics={"combined_score": score}))
    return ind


def ctx_with(store, **budget):
    return EvolveContext(store=store, budget=Budget(**(budget or {"max_items": 40})),
                         objective="toy")


# ------------------------------------------------------- the parent set ---
def test_every_selected_node_is_a_parent():
    """`parent_ids=list(task.inspiration_ids)` -- engine/runtime.py.

    Upstream has no distinguished parent. The selected set IS the parent set, which is what makes
    the lineage a multi-parent DAG and is why their value backpropagation is described as
    DAG-aware. Treating the first as the base program turns a synthesis into a mutation.
    """
    s = Store()
    seeds = [node(s, 0.1 * i, f"n{i}") for i in range(8)]
    m = SimpleTES(num_chains=1, k_candidates=3, num_inspirations=4, seed=0)
    c = ctx_with(s)
    asyncio.run(m.start(c, seeds))
    items = asyncio.run(m.ask(c, 1))
    assert items, "expected a batch"
    it = items[0]
    assert len(it.parent_ids) > 1, "a single parent means the selected set was not the parent set"
    assert len(it.parent_ids) == len(it.context.parents)
    assert {p.id for p in it.context.parents} == set(it.parent_ids)
    assert not it.context.inspirations, (
        "inspirations are not a separate channel upstream -- they are the parents")


def test_the_prompt_asks_for_a_synthesis_when_there_are_several_parents():
    """Upstream's generation prompt shows `[SAMPLED INSPIRATIONS]` and asks for a NEW program --
    it never names a current one. A prompt that presents the first parent as the incumbent would
    quietly make this a mutation of that one program."""
    from pantheon.evolution.core.genome import CodeGenome
    from pantheon.evolution.core.work import Create, PromptContext
    from pantheon.evolution.variators.completion import CompletionVariator

    s = Store()
    parents = []
    for i in range(3):
        ind = s.add(Individual(genome=CodeGenome(files={"main.py": f"# v{i}\nX = {i}\n"}),
                               kind="code"))
        ind.measurements.append(Measurement(individual_id=ind.id,
                                            metrics={"combined_score": 0.1 * i}))
        parents.append(ind)

    v = CompletionVariator()
    item = Create(kind="code", parent_ids=[p.id for p in parents],
                  context=PromptContext(instruction="go", parents=parents))
    prompt = v.build_prompt(ctx_with(s), item, "main.py", "# v0\nX = 0\n")
    assert "Reference programs" in prompt
    assert "Current program" not in prompt
    for i in range(3):
        assert f"X = {i}" in prompt, f"reference {i} was not shown"

    single = Create(kind="code", parent_ids=[parents[0].id],
                    context=PromptContext(instruction="go", parents=[parents[0]]))
    assert "Current program" in v.build_prompt(ctx_with(s), single, "main.py", "# v0\n"), (
        "one parent is still an edit of that program"
    )


# ----------------------------------------------------------- selection ---
def test_balance_always_keeps_the_incumbent_and_draws_the_rest_in_tiers():
    """`balanced_sample` -- policies/balance.py. `result = [nodes[0]]` then a three-way roll
    between the elite head, the middle band and the whole pool."""
    s = Store()
    chain = [node(s, 1.0 - 0.01 * i, f"n{i}") for i in range(40)]     # already best-first
    sel = Selector(exploitation_ratio=1.0, exploration_ratio=0.0, elite_ratio=0.2)
    picked = sel.pick(chain, 5, random.Random(0), "combined_score")
    assert picked[0] is chain[0], "the incumbent is always included, first"
    elite_end = max(1, int(len(chain) * 0.2))
    assert all(p in chain[:elite_end] for p in picked[1:]), (
        "with exploitation at 1.0 every other draw comes from the elite head")

    sel_wide = Selector(exploitation_ratio=0.0, exploration_ratio=0.0)
    wide = sel_wide.pick(chain, 6, random.Random(1), "combined_score")
    assert any(p not in chain[:elite_end] for p in wide[1:]), (
        "with exploitation at 0 the draws should reach past the elite head")


def test_a_short_chain_is_returned_whole():
    """`if len(nodes) <= n: return list(nodes)` -- there is nothing to sample from."""
    s = Store()
    chain = [node(s, 0.5, "a"), node(s, 0.4, "b")]
    assert len(Selector().pick(chain, 5, random.Random(0), "combined_score")) == 2


def test_the_chain_is_read_best_first():
    """`_get_chain_nodes` returns the score-sorted view, and every selector reads position 0 as
    the incumbent. Handing them the chain in creation order silently changes who that is."""
    s = Store()
    seeds = [node(s, 0.2, "low"), node(s, 0.9, "high"), node(s, 0.5, "mid")]
    m = SimpleTES(num_chains=1, seed=0)
    c = ctx_with(s)
    asyncio.run(m.start(c, seeds))
    order = [n.metrics()["combined_score"] for n in m._chain_nodes(c, 0)]
    assert order == sorted(order, reverse=True)


def test_the_inspiration_count_can_be_sampled_per_batch():
    """`_sample_inspiration_count` draws between min and max when both are set, so successive
    prompts see different amounts of history."""
    m = SimpleTES(num_inspirations=5, min_inspirations=2, max_inspirations=6, seed=0)
    counts = {m._inspiration_count(20) for _ in range(40)}
    assert len(counts) > 1, "a fixed count means the sampling never happens"
    assert min(counts) >= 2 and max(counts) <= 6

    fixed = SimpleTES(num_inspirations=5, seed=0)
    assert {fixed._inspiration_count(20) for _ in range(10)} == {5}


# ------------------------------------------------------------ budgets ---
def test_each_chain_gets_a_share_of_the_run_and_retires():
    """`chain_prompt_count >= prompt_budget` removes a chain from `_ready_chains`.

    Counted in PROMPTS. Upstream divides a generation budget by k because its budget counts
    children; one work item here already is one prompt, so dividing again retires every chain
    after a single batch.
    """
    s = Store()
    seeds = [node(s, 0.4, "seed")]
    m = SimpleTES(num_chains=3, k_candidates=4, seed=0)
    c = ctx_with(s, max_items=12)
    asyncio.run(m.start(c, seeds))
    assert sum(m.prompt_budget.values()) == 12, (
        "the shares must add up to the budget, or the leftover items belong to no chain and the "
        "run stops early while claiming the budget stopped it")
    assert m.prompt_budget == {0: 4, 1: 4, 2: 4}

    m.prompt_count[0] = 4
    assert m._retired(0) and not m._retired(1)


def test_the_remainder_is_handed_out_not_dropped():
    s = Store()
    m = SimpleTES(num_chains=4, seed=0)
    c = ctx_with(s, max_items=10)
    asyncio.run(m.start(c, [node(s, 0.4, "seed")]))
    assert sum(m.prompt_budget.values()) == 10
    assert sorted(m.prompt_budget.values()) == [2, 2, 3, 3]


# ----------------------------------------------------------- failures ---
def test_failure_patterns_are_kept_per_chain():
    """`_get_top_failures(chain_idx, top_k=10)`. A pattern that keeps breaking one line of attack
    is not evidence about a different one, and pooling them puts noise in every prompt."""
    from pantheon.evolution.core.work import Failure

    s = Store()
    seeds = [node(s, 0.4, "seed")]
    m = SimpleTES(num_chains=2, k_candidates=2, seed=0)
    c = ctx_with(s)
    asyncio.run(m.start(c, seeds))
    items = asyncio.run(m.ask(c, 2))
    assert len(items) == 2
    asyncio.run(m.on_failed(c, Failure(item_id=items[0].id, batch_id=items[0].batch_id,
                                       reason="timeout")))
    chain0 = items[0].meta["chain"]
    assert m.failures[chain0] == {"timeout": 1.0}
    assert m.failures[1 - chain0] == {}, "the other chain must not inherit it"


def test_state_from_before_failures_were_per_chain_still_loads():
    m = SimpleTES(num_chains=2, seed=0)
    m.load_state_dict({"chains": [[], []], "failures": {"boom": 3.0}})
    assert all(f == {"boom": 3.0} for f in m.failures), (
        "an older checkpoint carried one shared dict; refusing to load it would strand the run")


def test_state_round_trips():
    import json

    s = Store()
    m = SimpleTES(num_chains=2, k_candidates=2, selector="rpucg", seed=0)
    c = ctx_with(s, max_items=8)
    asyncio.run(m.start(c, [node(s, 0.4, "seed")]))
    m.prompt_count[0] = 2
    m.failures[1]["oops"] = 2.0
    clone = SimpleTES(num_chains=2, k_candidates=2, selector="rpucg", seed=0)
    clone.load_state_dict(json.loads(json.dumps(m.state_dict())))
    assert clone.prompt_budget == m.prompt_budget
    assert clone.prompt_count[0] == 2
    assert clone.failures[1] == {"oops": 2.0}


@pytest.mark.parametrize("name,cls", [("puct", PUCTSelector), ("rpucg", RPUCGSelector)])
def test_the_tree_selectors_bump_visits_for_everything_they_pick(name, cls):
    s = Store()
    chain = [node(s, 0.5 + 0.01 * i, f"n{i}") for i in range(6)]
    sel = cls()
    picked = sel.pick(chain, 3, random.Random(0), "combined_score")
    assert len(picked) == 3
    assert all(sel.visits[p.id] == 1 for p in picked)
    assert sum(sel.visits.values()) == 3, "only the selected nodes are visited"
