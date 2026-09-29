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


# -------------------------------------------------------------- puct ---
# `Q(s) + c * scale * P(s) * sqrt(1+T) / (1+n(s))` -- policies/puct.py. The first port used
# `q + c * sqrt(log(T+1) / (1+n))`, which is a different algorithm: no scale, no prior, no
# backpropagated Q, and visits shared across chains and counted at the wrong moment.

def descending(store, scores, tag="a"):
    """A chain, best first.

    `tag` because `Store.add` deduplicates by genome content and returns the EXISTING individual --
    two chains built with the same labels are silently the same five objects, and a test comparing
    them compares one chain with itself.
    """
    return [node(store, v, f"{tag}{i}") for i, v in enumerate(sorted(scores, reverse=True))]


def test_the_exploration_term_is_scaled_to_the_chains_score_range():
    """`scale = r_max - r_min`.

    Without it the bonus is in raw units while Q is a score in [0,1]. On a chain whose scores span
    0.02 the unscaled bonus is tens of times the differences it is perturbing, every node's u is
    dominated by its visit count, and the policy degenerates into round-robin.
    """
    s = Store()
    wide = descending(s, [0.9, 0.7, 0.5, 0.3, 0.1], "w")
    narrow = descending(s, [0.52, 0.515, 0.51, 0.505, 0.5], "n")
    sel = PUCTSelector(c=1.0)
    wide_bonus = [b for _, b in sel.terms(wide, "combined_score", 0)]
    narrow_bonus = [b for _, b in sel.terms(narrow, "combined_score", 0)]
    assert wide_bonus[0] == pytest.approx(0.8 * narrow_bonus[0] / 0.02, rel=1e-6), (
        "the bonus should scale linearly with the chain's score range")
    assert max(narrow_bonus) < 0.02, (
        "on a narrow chain the bonus must stay comparable to the score differences")


def test_the_prior_falls_off_with_rank():
    """`P(s) = (n - rank) / (n(n+1)/2)` -- the exploration budget is not split evenly."""
    s = Store()
    chain = descending(s, [0.9, 0.8, 0.7, 0.6])
    terms = PUCTSelector(c=1.0).terms(chain, "combined_score", 0)
    bonuses = [b for _, b in terms]
    assert bonuses == sorted(bonuses, reverse=True)
    assert bonuses[0] == pytest.approx(4 * bonuses[3], rel=1e-6), "priors are 4/10 ... 1/10"


def test_q_takes_the_best_score_any_child_reached():
    """`Q(s) = max(R(s), max_child_reward[s])`.

    A node that scored poorly but whose batch produced the run's best program is worth returning
    to. Reading only the node's own score throws that away.
    """
    s = Store()
    chain = descending(s, [0.9, 0.4])
    sel = PUCTSelector(c=0.0)                       # bonus off, so u is exactly Q
    assert [q for q, _ in sel.terms(chain, "combined_score", 0)] == [0.9, 0.4]
    sel.on_commit(0, [chain[1].id], best_score=0.95)
    assert [q for q, _ in sel.terms(chain, "combined_score", 0)] == [0.9, 0.95]


def test_visits_land_on_commit_and_stay_inside_their_chain():
    """`_finalize_hook_locked` bumps the parents of a batch that has come back.

    Counting at selection marks a node explored before its batch has reported anything, and one
    dict shared across chains lets one chain's history suppress another's.
    """
    s = Store()
    chain = descending(s, [0.9, 0.8, 0.7])
    sel = PUCTSelector()
    picked = sel.pick(chain, 2, random.Random(0), "combined_score", 0)
    assert sel.visits == {}, "selection alone must not count as a visit"

    sel.on_commit(0, [p.id for p in picked], best_score=0.95)
    assert sel.visits[0] == {p.id: 1 for p in picked}
    assert sel.expansions[0] == 1
    assert 1 not in sel.visits, "chain 1 must not inherit chain 0's counts"

    before = [b for _, b in sel.terms(chain, "combined_score", 0)]
    sel.on_commit(0, [picked[0].id], best_score=0.5)
    after = [b for _, b in sel.terms(chain, "combined_score", 0)]
    i = chain.index(picked[0])
    assert after[i] < before[i], "a node that was just used should lose exploration bonus"


def test_the_method_reports_commits_to_its_selector():
    """The counts are useless if nothing ever calls the hook."""
    s = Store()
    m = SimpleTES(num_chains=1, k_candidates=2, selector="puct", seed=0)
    c = ctx_with(s)
    seeds = [node(s, 0.4, "seed"), node(s, 0.6, "b")]
    asyncio.run(m.start(c, seeds))
    items = asyncio.run(m.ask(c, 1))
    it = items[0]
    for score in (0.7, 0.5):
        child = s.add(Individual(genome=TextGenome(text=f"cand {score}", kind="code"),
                                     kind="code"))
        child.measurements.append(Measurement(individual_id=child.id, batch_id=it.batch_id,
                                              metrics={"combined_score": score}))
        asyncio.run(m.on_measured(c, child, child.measurements[-1]))
    sel = m.selector
    assert sel.expansions.get(0) == 1
    assert set(sel.visits.get(0, {})) == set(it.parent_ids)
    assert all(v == 0.7 for v in sel.max_child[0].values()), "best of the batch backpropagates"


@pytest.mark.parametrize("cls", [PUCTSelector, RPUCGSelector])
def test_selector_state_round_trips(cls):
    import json

    s = Store()
    chain = descending(s, [0.9, 0.8])
    sel = cls()
    sel.on_commit(0, [chain[0].id], best_score=0.93)
    clone = cls()
    clone.load_state_dict(json.loads(json.dumps(sel.state_dict())))
    assert clone.terms(chain, "combined_score", 0) == sel.terms(chain, "combined_score", 0)


# ------------------------------------------------------------- rpucg ---
# `Q + c * P * sqrt(1+T) / (1+n)` on percentile ranks, over a V propagated through the DAG --
# policies/rpucg.py. Ours used to be PUCT with a depth counter, which is none of that.

def line(store, scores, tag="v"):
    """A descent chain: each node's parent is the one before it."""
    out = []
    for i, v in enumerate(scores):
        ind = store.add(Individual(genome=TextGenome(text=f"{tag}{i}", kind="code"), kind="code",
                                   parent_ids=[out[-1].id] if out else []))
        ind.measurements.append(Measurement(individual_id=ind.id,
                                            metrics={"combined_score": v}))
        out.append(ind)
    return out


def test_value_propagates_up_the_dag_with_gamma():
    """`V(s) = max(raw(s), gamma * max over children of V(c))`.

    A node is worth what the best thing descended from it is worth, discounted once per generation
    of distance. PUCT's `max_child_reward` reaches one hop and never decays; this reaches the whole
    line, which is the difference the name is about.
    """
    s = Store()
    chain = line(s, [0.10, 0.20, 0.90])          # a poor root leading to a very good descendant
    sel = RPUCGSelector(gamma=0.5)
    sel.observe(ctx_with(s), "combined_score")

    # leaf keeps its own; parent gets 0.5*0.9; grandparent 0.5*0.45, both beating their own scores
    assert sel._q[chain[2].id] > sel._q[chain[1].id] > sel._q[chain[0].id]
    ranks = sorted(sel._q.values())
    assert ranks == [0.0, 1 / 3, 2 / 3], "Q is a percentile rank in [0, 1)"

    flat = RPUCGSelector(gamma=0.0)              # no propagation: V collapses to the raw score
    flat.observe(ctx_with(s), "combined_score")
    assert flat._q[chain[0].id] == 0.0 and flat._q[chain[2].id] == 2 / 3


def test_q_ranks_value_while_p_ranks_the_raw_score():
    """Both are percentile ranks, but of different things -- which is the point of having two.

    A node can be valuable for what came after it and unremarkable in itself; the exploration
    budget follows what it scored, not what its descendants did.
    """
    s = Store()
    chain = line(s, [0.10, 0.95])
    sel = RPUCGSelector(gamma=0.9)
    sel.observe(ctx_with(s), "combined_score")
    root = chain[0].id
    assert sel._q[root] == 0.0 and sel._p[root] == 0.0
    assert sel._q[root] < sel._q[chain[1].id]
    # V(root) = 0.9*0.95 = 0.855, well above its own 0.10, but P still sees 0.10
    assert sel._p[chain[1].id] > sel._p[root]


def test_no_scale_factor_because_the_ranks_are_already_comparable():
    """PUCT needs `r_max - r_min` to keep its bonus in the same units as Q. Ranks are in [0,1) by
    construction, so rpucg has no scale term -- and a chain whose scores span 0.001 behaves the
    same as one that spans 0.9."""
    s1, s2 = Store(), Store()
    wide, narrow = line(s1, [0.1, 0.5, 0.9], "w"), line(s2, [0.500, 0.501, 0.502], "n")
    out = []
    for store, chain in ((s1, wide), (s2, narrow)):
        sel = RPUCGSelector()
        sel.observe(ctx_with(store), "combined_score")
        out.append(sel.terms(list(reversed(chain)), "combined_score", 0))
    assert out[0] == out[1], "only the ORDER of the scores should matter, not their spread"


def test_taking_a_node_rules_out_its_parents_and_children():
    """The greedy loop excludes the 1-hop neighbourhood, so a prompt is not built out of one
    family line."""
    s = Store()
    chain = line(s, [0.1, 0.2, 0.3, 0.4])        # 0 -> 1 -> 2 -> 3
    sel = RPUCGSelector()
    sel.observe(ctx_with(s), "combined_score")
    picked = sel.pick(list(reversed(chain)), 3, random.Random(0), "combined_score", 0)
    ids = [p.id for p in picked]
    kin = {chain[i].id: {chain[j].id for j in (i - 1, i + 1) if 0 <= j < len(chain)}
           for i in range(len(chain))}
    for a in ids:
        assert not (kin[a] & set(ids) - {a}), f"{a} was picked alongside a direct relative"


def test_rpucg_keeps_no_max_child_reward():
    """`_finalize_hook_locked` moves visit counts and nothing else -- the V propagation in
    `observe` is this policy's backpropagation, and it reads the store directly."""
    s = Store()
    chain = line(s, [0.4, 0.6])
    sel = RPUCGSelector()
    sel.on_commit(0, [chain[0].id], best_score=0.99)
    assert sel.visits[0] == {chain[0].id: 1}
    assert sel.expansions[0] == 1
    assert not sel.max_child.get(0), "rpucg does not track a per-node best child"


def test_the_method_hands_the_population_to_a_selector_that_needs_it():
    """`observe` is useless if `ask` never calls it: without it rpucg scores every node 0."""
    s = Store()
    chain = line(s, [0.3, 0.9])
    m = SimpleTES(num_chains=1, k_candidates=2, selector="rpucg", seed=0)
    c = ctx_with(s)
    asyncio.run(m.start(c, chain))
    asyncio.run(m.ask(c, 1))
    assert m.selector._q, "the selector never saw the population"
    assert set(m.selector._q) == {i.id for i in s}
