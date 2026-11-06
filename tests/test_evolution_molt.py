"""Molt's rules: when it plans, where a direction's child goes, and what a sprout's probation does.

The planner and dedup calls are replaced by canned replies, so these tests pin the bookkeeping, not
the model.
"""
from __future__ import annotations

import asyncio
import json

from pantheon.evolution.core import Budget, EvolveContext, Individual, Measurement, Store, TextGenome
from pantheon.evolution.core.work import Create, Failure, PromptContext
from pantheon.evolution.methods.molt import ESTABLISHED, RETIRED, SPROUT, Molt


def ctx_with(store):
    return EvolveContext(store=store, budget=Budget(max_items=400), objective="toy")


def child(store, item, score, i=0):
    ind = store.add(Individual(genome=TextGenome(text=f"{item.batch_id}-{i}-{score}", kind="code"),
                               kind="code", parent_ids=list(item.parent_ids)))
    m = Measurement(individual_id=ind.id, metrics={"combined_score": score},
                    batch_id=item.batch_id)
    ind.measurements.append(m)
    return ind, m


def make(store, **kw):
    seed = store.add(Individual(genome=TextGenome(text="seed", kind="code"), kind="code"))
    seed.measurements.append(Measurement(individual_id=seed.id, metrics={"combined_score": 1.0}))
    opts = dict(num_chains=2, k_candidates=2, num_inspirations=2, planners=1,
                directions_per_planner=2, stall_evals=4, probation_turns=3, seed=0)
    opts.update(kw)
    m = Molt(**opts)
    c = ctx_with(store)
    asyncio.run(m.start(c, [seed]))
    return m, c, seed


def plan_reply(target, types=("redesign", "integration")):
    return json.dumps({"diagnosis": "stuck", "directions": [
        {"type": t, "target_branch_id": target, "task": f"try {t} {j}",
         "mechanism_hypothesis": "h", "donor_branch_ids": []} for j, t in enumerate(types)]})


def fake_llm(m, target, types=("redesign", "integration")):
    calls = []

    async def llm(prompt):
        calls.append(prompt)
        if "removing only obvious semantic duplicates" in prompt:
            return ""                         # no dedup verdict: keep everything
        return plan_reply(target, types)

    m._llm = llm
    return calls


def resolve(m, c, items, score):
    ids = {}
    for it in items:
        for i in range(it.k):
            ind, meas = child(c.store, it, score, i)
            ids.setdefault(it.batch_id, []).append(ind.id)
            asyncio.run(m.on_measured(c, ind, meas))
    return ids


def run_until_stall(m, c, score=1.0):
    """Ordinary batches that never improve, until a planning round has happened."""
    for _ in range(20):
        items = asyncio.run(m.ask(c, 2))
        if any("molt_direction" in (it.meta or {}) for it in items):
            return items
        resolve(m, c, items, score)
    raise AssertionError("no planning round")


def test_no_planning_before_the_stall_window():
    s = Store()
    m, c, seed = make(s, stall_evals=50)
    calls = fake_llm(m, seed.id)
    for _ in range(5):
        resolve(m, c, asyncio.run(m.ask(c, 2)), 1.0)
    assert not calls, "planned before stall_evals non-improving children"


def test_a_redesign_opens_a_sprout_and_an_edit_joins_its_target_chain():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id)
    items = run_until_stall(m, c)
    dirs = [it for it in items if "molt_direction" in it.meta]
    assert {it.meta["molt_type"] for it in dirs} == {"redesign", "integration"}
    n_before = len(m.chains)
    ids = resolve(m, c, dirs, 0.5)
    sprouts = [k for k, r in m.role.items() if r == SPROUT]
    assert len(sprouts) == 1 and len(m.chains) == n_before + 1
    red = next(it for it in dirs if it.meta["molt_type"] == "redesign")
    integ = next(it for it in dirs if it.meta["molt_type"] == "integration")
    assert m.chains[sprouts[0]] == ids[red.batch_id], "the redesign's program starts the sprout"
    edit = ids[integ.batch_id][0]
    assert all(edit in m.chains[k] for k in (0, 1)), "an edit's child joins its target's chains"
    assert edit not in m.chains[sprouts[0]]


def test_no_new_planning_while_a_sprout_is_on_probation():
    s = Store()
    m, c, seed = make(s, probation_turns=50)
    calls = fake_llm(m, seed.id, types=("redesign",))
    items = run_until_stall(m, c)
    resolve(m, c, items, 0.5)
    n_plans = sum("Dynamic branch intervention" in p for p in calls)
    for _ in range(10):
        resolve(m, c, asyncio.run(m.ask(c, 3)), 0.9)
    assert sum("Dynamic branch intervention" in p for p in calls) == n_plans


def _grow_sprout(m, c, sp, score):
    """Feed chain `sp` children of `score` (everything else 1.0) until its probation is decided."""
    for _ in range(30):
        if m.role[sp] != SPROUT:
            return
        items = asyncio.run(m.ask(c, 3))
        resolve(m, c, [it for it in items if it.meta.get("chain") == sp], score)
        resolve(m, c, [it for it in items if it.meta.get("chain") != sp], 1.0)


def test_a_sprout_near_the_champion_graduates():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 0.5)
    sp = next(k for k, r in m.role.items() if r == SPROUT)
    _grow_sprout(m, c, sp, 0.995)                 # within 1 % of the champion (1.0)
    assert m.role[sp] == ESTABLISHED
    assert m.tried and m.tried[-1]["fate"] == "graduated"


def test_a_sprout_far_below_is_retired_and_gets_no_more_turns():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 0.5)
    sp = next(k for k, r in m.role.items() if r == SPROUT)
    _grow_sprout(m, c, sp, 0.6)
    assert m.role[sp] == RETIRED
    count = m.prompt_count[sp]
    for _ in range(5):
        resolve(m, c, asyncio.run(m.ask(c, 3)), 1.0)
    assert m.prompt_count[sp] == count, "a retired chain was asked for work"


def test_shedding_keeps_the_cap_and_never_the_champion():
    s = Store()
    m, c, seed = make(s, max_established=2)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 0.5)
    sp = next(k for k, r in m.role.items() if r == SPROUT)
    _grow_sprout(m, c, sp, 1.5)                   # the sprout overtakes everything
    active = [k for k, r in m.role.items() if r in ("base", ESTABLISHED)]
    assert len(active) == 2 and sp in active, "the overtaking sprout must survive the shed"
    # a weaker graduate later sheds a base chain, never the champion's chain
    assert m.role[sp] == ESTABLISHED


def test_state_round_trip():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id)
    resolve(m, c, run_until_stall(m, c), 0.5)
    st = json.loads(json.dumps(m.state_dict()))
    m2 = Molt(num_chains=2, k_candidates=2, num_inspirations=2, planners=1, seed=0)
    m2.load_state_dict(st)
    assert m2.role == m.role and m2.chains == m.chains and m2.tried == m.tried
    assert m2.sprouts.keys() == m.sprouts.keys()


def fail(m, c, items, stage="generate"):
    """Every child of `items` dies with `stage` (generate = the model returned nothing)."""
    for it in items:
        for _ in range(it.k):
            asyncio.run(m.on_failed(c, Failure(item_id=it.id, batch_id=it.batch_id, stage=stage,
                                               reason="HTTP 402")))


def test_a_silent_model_is_not_a_stall():
    s = Store()
    m, c, seed = make(s)
    calls = fake_llm(m, seed.id)
    for _ in range(20):
        fail(m, c, asyncio.run(m.ask(c, 2)))
    assert not calls, "an outage (generate failures) triggered planning"
    for _ in range(10):
        fail(m, c, asyncio.run(m.ask(c, 2)), stage="evaluate")
    assert calls, "programs that crash are attempts and must open a stall window"


def test_a_silent_model_does_not_spend_a_sprouts_probation():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 0.5)
    sp = next(k for k, r in m.role.items() if r == SPROUT)
    for _ in range(20):
        fail(m, c, asyncio.run(m.ask(c, 3)))
    assert m.role[sp] == SPROUT and m._turns(sp) == 1, "an outage decided a probation"
    _grow_sprout(m, c, sp, 0.995)
    assert m.role[sp] == ESTABLISHED


def test_an_unwritten_redesign_is_not_a_tried_design():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    fail(m, c, run_until_stall(m, c))
    assert not m.tried and not any(r == SPROUT for r in m.role.values())
    assert m.interventions[-1]["children"][0].get("not_produced")


def test_an_old_checkpoint_counts_turns_as_before():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 0.5)
    st = json.loads(json.dumps(m.state_dict()))
    del st["molt"]["turns"]
    m2 = Molt(num_chains=2, k_candidates=2, num_inspirations=2, planners=1, seed=0)
    m2.load_state_dict(st)
    assert m2.turns == {int(k): v for k, v in st["prompt_count"].items()}


class _Plateau:
    """A model whose children never beat the seed (1.0) -- a stall -- and whose redesigns land
    near it (0.995); `down=True` is a gateway outage: nothing comes back."""

    def __init__(self, down=False):
        self.down, self.n = down, 0

    async def create(self, ctx, item):
        from pantheon.evolution.core import Produced
        if self.down:
            return []
        self.n = max(self.n, len(ctx.store))
        out = []
        for _ in range(item.k):
            self.n += 1
            v = 0.995 if (item.meta or {}).get("molt_type") == "redesign" else 1.0
            out.append(Produced(genome=TextGenome(text=f"p{self.n}", kind="code", meta={"v": v}),
                                item_id=item.id, batch_id=item.batch_id,
                                parent_ids=list(item.parent_ids)))
        return out


class _Read:
    kind = "code"

    async def measure(self, ctx, ind, fidelity="full"):
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": float(ind.genome.meta.get("v", 1.0))})


def _phase(tmp_path, *, down, stop, items=400):
    """One session of a run, the way run_bench drives it: a fresh Molt, resumed from disk."""
    from pantheon.evolution.core.loop import evolve
    m = Molt(num_chains=2, k_candidates=2, num_inspirations=2, planners=1,
             directions_per_planner=1, stall_evals=4, probation_turns=3, seed=0)
    streak = {"n": 0}

    def on_event(kind, data):
        if kind == "failed":
            streak["n"] = streak["n"] + 1 if data.get("stage") == "generate" else 0
        elif kind == "measured":
            streak["n"] = 0

    async def llm(prompt):
        if down or "removing only obvious semantic duplicates" in prompt:
            return ""
        best = max(ctx_best(m), key=lambda x: x[1])[0]
        return plan_reply(best, ("redesign",))

    def ctx_best(m):
        return [(i, 1.0) for c in m.chains for i in c] or [("", 0)]

    m._llm = llm
    seed = TextGenome(text="seed", kind="code", meta={"v": 1.0})
    asyncio.run(evolve(method=m, variator=_Plateau(down), evaluators={"code": _Read()},
                       seeds=[seed], objective="toy",
                       budget=Budget(max_items=items, stop_when=lambda: stop(m, streak["n"])),
                       concurrency=2, on_event=on_event, checkpoint_path=str(tmp_path),
                       checkpoint_every=1, resume=(tmp_path / "store.json").exists()))
    return m


def test_an_outage_stops_the_run_and_a_resume_carries_on(tmp_path):
    # 1. run until a redesign is on probation
    a = _phase(tmp_path, down=False, stop=lambda m, s: any(r == SPROUT for r in m.role.values()))
    sp = next(k for k, r in a.role.items() if r == SPROUT)
    before = (len(a.interventions), list(a.tried))
    # 2. the gateway goes down: the run stops on the streak, and the state is untouched by it
    b = _phase(tmp_path, down=True, stop=lambda m, s: s >= 40)
    assert b.role[sp] == SPROUT and b.turns.get(sp, 0) == a.turns.get(sp, 0)
    assert (len(b.interventions), b.tried) == before
    # 3. the gateway is back: the resumed run decides the probation and keeps searching
    c = _phase(tmp_path, down=False, stop=lambda m, s: m.role.get(sp) != SPROUT)
    assert c.role[sp] == ESTABLISHED and c.tried[-1]["fate"] == "graduated"


def _echo_sprout():
    """A redesign whose first program scores exactly like its reference (the seed, 1.0)."""
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 1.0)
    sp = next(k for k, r in m.role.items() if r == SPROUT)
    return m, c, sp


def test_a_redesign_that_echoes_its_reference_is_flagged_and_cannot_graduate_on_it():
    m, c, sp = _echo_sprout()
    assert m.sprouts[sp]["echo"] == 1.0
    assert any(k.startswith("NEW DESIGN NEVER RAN") for k in m.failures[sp]), \
        "the chain's next prompts must be told the new design never ran"
    _grow_sprout(m, c, sp, 1.0)                 # within the margin, but only on the fallback
    assert m.role[sp] == RETIRED
    assert m.tried[-1]["fate"] == "retired (never beat its fallback)"


def test_an_echo_whose_chain_beats_the_fallback_graduates():
    m, c, sp = _echo_sprout()
    _grow_sprout(m, c, sp, 1.2)
    assert m.role[sp] == ESTABLISHED


def test_records_carry_when_they_happened_and_lineage_names_the_winning_intervention():
    s = Store()
    m, c, seed = make(s)
    fake_llm(m, seed.id, types=("redesign",))
    resolve(m, c, run_until_stall(m, c), 0.5)
    sp = next(k for k, r in m.role.items() if r == SPROUT)
    _grow_sprout(m, c, sp, 1.5)
    iv = m.interventions[0]
    o = m.sprouts[sp]
    assert isinstance(iv["at"], int) and iv["at"] <= o["opened_at"] < o["decided_at"] <= m.attempts
    rep = json.loads(json.dumps(m.lineage(c.store)))
    assert rep["best_score"] == 1.5
    assert {"chain": sp, "origin": "redesign", "intervention": 0}.items() <= rep["best_chains"][0].items()
    assert rep["interventions"][0]["sprouts"][0]["fate"] == "graduated"


# ---- the prompts a planned direction and a sprout chain are written with ----------------

def _code(body):
    from pantheon.evolution.core import CodeGenome
    return CodeGenome(files={"main.cpp": f"#include <x>\n// EVOLVE-BLOCK-START\n{body}\n"
                                         f"// EVOLVE-BLOCK-END\nint main(){{}}\n"})


def _prompt_world():
    """A seed (short), a polished champion, a donor, one rejected attempt; a Molt over them."""
    s = Store()
    seed = s.add(Individual(genome=_code("int a;"), kind="code"))
    champ = s.add(Individual(genome=_code("int champion_algorithm_" + "x" * 400 + ";"),
                             kind="code", parent_ids=[seed.id]))
    donor = s.add(Individual(genome=_code("int donor_trick;"), kind="code", parent_ids=[seed.id]))
    bad = s.add(Individual(genome=_code("int broken;"), kind="code", parent_ids=[champ.id]))
    for ind, v, why in ((seed, 1.0, None), (champ, 2.0, None), (donor, 1.5, None),
                        (bad, 0.0, "7/150 cases rejected -- case 4: TLE")):
        mt = {"combined_score": v, **({"invalid_reason": why} if why else {})}
        ind.measurements.append(Measurement(individual_id=ind.id, metrics=mt))
    m = Molt(num_chains=2, k_candidates=2, num_inspirations=2, planners=1, seed=0)
    c = ctx_with(s)
    asyncio.run(m.start(c, [seed]))
    m.chains[0] = [seed.id, champ.id, donor.id, bad.id]
    return m, c, seed, champ, donor, bad


def _render(m, c, item):
    from pantheon.evolution.variators.completion import EvolveBlock
    v = m.default_variator(target_file="main.cpp")
    eb = EvolveBlock(item.context.parents[0].genome.files["main.cpp"])
    return v.build_block_prompt(c, item, "main.cpp", eb)


def test_molt_builds_its_own_operator():
    from pantheon.evolution.methods.molt import MoltVariator
    m = Molt(num_chains=2, k_candidates=2, num_inspirations=2, seed=0)
    assert isinstance(m.default_variator(), MoltVariator)


def test_a_redesign_prompt_shows_an_io_reference_not_the_incumbent():
    m, c, seed, champ, donor, bad = _prompt_world()
    d = {"id": "i0/planner_0/direction_00", "intervention": 0, "type": "redesign",
         "target_branch_id": champ.id, "task": f"Write a complete replacement of {champ.id}",
         "mechanism_hypothesis": "h", "evidence_attempt_ids": [bad.id],
         "frontier_case": {"required_gain": 0.1, "why_plausible": "because"}}
    it = m._direction_item(c, d)
    assert it.parent_ids == [champ.id], "lineage: the redesign descends from what it replaces"
    assert [p.id for p in it.context.parents] == [seed.id], "shown: the shortest valid program"
    text = _render(m, c, it)
    assert f"[I/O REFERENCE (format and helpers only)] branch {seed.id}" in text
    assert "champion_algorithm_" not in text, "the incumbent's code must not be shown"
    assert champ.id in text and "COMPLETE NEW PROGRAM" in text
    assert "higher score" not in text and "Learn from these" not in text
    assert "7/150 cases rejected -- case 4: TLE" in text, "cited attempts are quoted"
    assert "0.1" in text and "because" in text
    assert "EVOLVE-BLOCK-START" in text and "EXACT_PREFIX" in text


def test_an_edit_prompt_labels_target_and_donor_by_id():
    m, c, seed, champ, donor, bad = _prompt_world()
    d = {"id": "i0/planner_0/direction_01", "intervention": 0, "type": "integration",
         "target_branch_id": champ.id, "donor_branch_ids": [donor.id],
         "task": f"Port {donor.id}'s trick into {champ.id}", "mechanism_hypothesis": "h"}
    text = _render(m, c, m._direction_item(c, d))
    assert f"[TARGET (edit this one)] branch {champ.id}" in text
    assert f"[DONOR] branch {donor.id}" in text
    assert "Implement exactly this experiment on the TARGET program." in text


def test_a_sprout_chain_prompt_carries_its_design_and_rejections():
    m, c, seed, champ, donor, bad = _prompt_world()
    sp = m._new_chain()
    m.chains[sp] = [bad.id]
    m.sprouts[sp] = {"task": "skeleton-and-repair solver", "hypothesis": "keeps more zeros",
                     "old": 2.0, "fate": "probation", "echo": None}
    brief = m._design_brief(c, sp)
    it = Create(kind="code", parent_ids=[bad.id], k=2,
                context=PromptContext(instruction="toy", parents=[bad], history="", failures={}),
                meta={"chain": sp, "molt_design": brief})
    text = _render(m, c, it)
    assert text.index("THIS CHAIN'S DESIGN") < text.index("Generation instruction")
    assert "skeleton-and-repair solver" in text and "turn 1 of 5" in text
    assert "rejected: 7/150 cases rejected -- case 4: TLE" in text
    assert "[SAMPLED INSPIRATIONS]" in text, "the rest is SimpleTES's prompt"
    assert "Prioritize NOVEL" not in text and "higher score" not in text
    assert text.rstrip().endswith("Generate the next version of this design:")


def test_planners_see_why_an_attempt_was_rejected():
    m, c, seed, champ, donor, bad = _prompt_world()
    attempts = m._views(c)[3]
    assert any("rejected: 7/150 cases rejected" in a for a in attempts)
