# What the Erdős runs established

Twenty runs, three search policies, one problem. Written down because most of what came out of it
was not what the experiments were designed to measure, and because several things I asserted along
the way turned out to be wrong — those are recorded here too, with what refuted them.

Raw data is in `results_compare*/` (gitignored; `store.json` holds every individual and
measurement). Configuration for each run is in its `summary.json`.

---

## The headline: this problem can no longer tell these methods apart

Three policies — `annealed` (new), `idea_code` (old), `map_elites` (established baseline) — with
the operator, warm start, model, budget, evaluator and seed set all held fixed.

| Ψ (lower is better) | action budget 14 | action budget 28 |
|---|---|---|
| `annealed` | **0.381393**  (0.381314, 0.381471) | 0.382555  (0.382697, 0.382414) |
| `idea_code` | 0.383071  (0.383894, 0.382248) | 0.382600  (0.382406, 0.382793) |
| `map_elites` | 0.382191  (0.382311, 0.382071) | **0.382281**  (0.382164, 0.382397) |

At budget 14 `annealed` separated completely — both its seeds beat both seeds of both other arms,
with a between-arm gap 3.3× the largest within-arm spread. At budget 28 the ordering **reversed**
and everything collapsed into a range (0.382281–0.382600) no wider than the arms' own seed-to-seed
spreads.

**Two runs at n=2 giving opposite orderings is the result.** The arm differences are noise. The
budget-14 separation was luck, and I said at the time it was "the strongest possible result at
n=2" — that was wrong, and the way to have known was to not treat a complete separation at n=2 as
evidence in the first place.

All three arms sit at 0.3821–0.3826 against a recorded best of 0.380909. Nothing done to the
search moved that. Separating a 0.0003 effect from 0.0003 of noise needs 5–8 seeds per arm; at
roughly an hour per run that is 15–24 hours to answer a question this problem may not be able to
answer at all.

---

## What did hold up

### 1. Feasibility is a function of the action budget

Not of the prompt, not of the operator class, not of a submit-time gate. Doubling the per-mutation
action budget:

| | budget 14 | budget 28 |
|---|---|---|
| `annealed` | 10/53 = 18.9% | **0/54 = 0.0%** |
| `idea_code` | 5/44 = 11.4% | 2/44 = 4.5% |
| `map_elites` | 4/63 = 6.3% | 2/64 = 3.1% |
| **total** | 19/160 = 11.9% | **4/162 = 2.5%** |

Fisher exact p = 0.00099. Instrumentation confirms the cap was binding: at 28 the agents spent a
mean of 23–24 calls and 12–15 mutations per arm still hit the ceiling, so at 14 they could not
finish an observe-and-correct cycle. Evaluator calls per program rose from 1.11–2.25 to 3.19–4.25.

The default is now 28.

Four earlier explanations were tested and refuted before this one:

| hypothesis | how it died |
|---|---|
| a blind completion submits invalid code; an agent would not | agent 13.3% vs completion 7.3% — the agent was *worse* |
| mutations cascade from infeasible parents | 1 of 24 in the worst arm |
| the instruction lacks a "verify before submitting" reminder | A/B'd: 32.6% → 25.5%, p = 0.50 |
| cross-idea code transplants force risky rewrites | the bad arm was worse in *every* class (same 30% vs 4%) |

### 2. Removing wasted work bought no quality

Doubling the budget eliminated 80% of infeasible submissions and cost roughly double the wall
clock. Mean Ψ across arms went 0.382218 → 0.382479 — no measurable change, in the wrong direction
if anything.

This is the second independent observation of the same thing. Earlier, `annealed` produced 65%
more feasible programs than `idea_code` and scored the same. **On a problem that plateaus, budget
efficiency does not convert into result quality**, which means efficiency work cannot be validated
here and has to be measured somewhere else.

### 3. The judge redesign works, as a predictor

Prediction against measured outcome went from Spearman **−0.15** (old judge, and the two numbers
were not even on the same scale) to **+0.49** and **+0.80** with slope near 1, mean absolute error
0.014–0.054 on gains of ~0.11. n = 10 and n = 5, neither significant alone; the point is that the
shared scale makes the question answerable at all.

---

## Bugs found, all of which were invisible in the printed numbers

1. **`success` is not `valid`.** The evaluator reports a violated constraint as `success=True,
   validity=0, score=0`. Reading that zero as a score had settled in four separate places:
   selection, the judge's training set, the history shown to the agent, and the operator's own
   commit path. `submit` checked nothing at all — it snapshotted the working directory, so an
   agent could verify one version, edit again, and commit the edit unchecked.

2. **A method silently outspent its peers.** `AnnealedIdeaCode.default_variator` dropped
   `max_tool_calls`, so its agent ran unlimited while the other arms ran on 14. That one missing
   kwarg produced most of what looked like a policy result — more evaluator calls, a sixth of the
   infeasible rate, 2.6× the wall clock. `map_elites` dropped `max_evaluations` the same way.
   `results_compare/` was produced under this mismatch and does not isolate what it claims to.

3. **An agent wrote an evolved `sequence.py` back into this source directory.** Its tools are not
   confined to the workspace. Every one of the 20 runs happens to have read the seed before that
   write — verified by hashing the seed genome stored in all 20 `store.json` — but nothing would
   have reported it.

4. **A relative `--output`** left the evaluator's workspace path unresolvable in its subprocess, so
   every evaluation died with `FileNotFoundError`, including the seed's, and the run continued
   silently with a zeroed baseline.

Three of these are now checked rather than trusted: a conformance test asserts every method
forwards its operator's knobs, and `compare_v2` records each arm's resolved operator config and
seed hash and refuses to present a comparison whose arms differ in either.

---

## Open

- The submit gate records an invalid program when it exhausts its retries and nothing valid was
  ever verified. At budget 14 that path produced 19 of 19 remaining infeasible programs. It should
  probably report a failure instead, so the method's accounting is right and the store stays clean.
- `min-max` normalisation in `AnnealedIdeaCode` maps the extremes of the candidate set to 0 and 1
  whatever the true spread, so with two candidates the search is greedy exactly when it should be
  broadest. `--norm absolute` exists as the alternative and has never been measured.
- The learned judge accumulates roughly ten labelled ideas per run, which is not a training set.
  `--judge-state` persists it across runs; that has never been exercised for more than one run.
