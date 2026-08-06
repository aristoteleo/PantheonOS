# Pantheon-Evolve 2 — A Redesign for Robust, Transparent, Cumulative Method Discovery

**Purpose.** Address the two Cell reviewers' core objection to Pantheon-Evolve — that it is a
competent-but-incremental re-assembly of existing LLM-guided code evolution whose headline
gains may be metric-overfitting rather than real method invention — by changing *what is
optimized and how validity is established*, not by tuning the existing loop. The redesign is
also the algorithmic contribution that lets us claim novelty *beyond* SimpleTES,
AutoScientists, and DeLM.

Audience: internal (rebuttal authoring + implementation). Method names/passages are written in
English so they can be pasted into the manuscript/response-to-reviewers.

---

## 0. TL;DR

The current Pantheon-Evolve maximizes a **fixed, human-authored scalar**
`f = Σ wᵢ·metricᵢ (+ LLM reviewer)` and then reports components of *that same scalar*. Every
serious reviewer criticism is a symptom of this one design choice (Goodhart's law), plus a
transparency gap. Both are structural, not presentational.

**Pantheon-Evolve 2 (PE2)** replaces the fixed scalar with a **minimax co-evolution between a
population of candidate methods and a population of adversarial falsifiers**, gated by a
**learned validity model** that separates real biological signal from known artifact classes,
with every accepted improvement distilled into a **transparent, reusable, cross-domain
"method-gene"** carrying a **generalization certificate**. A **learnable search policy** makes
the system improve at *how* it evolves across runs and turns the reproducibility question into a
reported distribution.

The one-line pitch for the rebuttal:

> *We no longer ask "did the score go up?"; we ask "can an adversary find any held-out study,
> technology, or orthogonal biological readout where this method collapses, and is the gain
> explained by a known artifact?" A method ships only when the answer is no, with a certificate.*

This is Goodhart-resistant **by construction**, and it is precisely the gap that SimpleTES
(names evaluator fidelity as its central limitation), AutoScientists (single-objective, no
learned policy), and DeLM (no evolution, verifies *evidence support* but not *biological
reality* or *OOD generalization*) all leave open.

---

## 1. Diagnosis — one root cause behind the reviews

### 1.1 What both reviewers actually said about Pantheon-Evolve

| # | Reviewer point | Root issue |
|---|---|---|
| R1-2f, R2-8, R2-10 | Objective = reported metric → **circular / metric-overfitting**; "beautiful not real"; batch-mixing improvable by **overcorrection** | Fixed proxy objective |
| R2-10 | Objective is a **hand-weighted scalar** (integration quality + bio-conservation + speed + convergence, with function hints) → "guided optimization, not method invention" | Human-designed objective |
| R2-10, R1-min1 | Train/val/test split of the **same cells** ≠ generalization across studies/tissues/technologies | No OOD protocol |
| R1-2h | **Black box**: user sees score rise, not *what changed and why* | No mechanistic output |
| R1-3 | **Single run**, no variance across seeds, no failure distribution; Harmony/BBKNN "more modest" unexplained | No distributional reporting |
| R1-4, R2-10 | "Super-human" vs **Scanorama-2019**, not vs an expert human + Claude Code in 2026 with the same harness | Wrong counterfactual |
| R2-9 | Resembles existing LLM genetic programming; analyzer/mutator/MAP-Elites/islands/MTP/LLM-reviewer **asserted, not ablated** as decisive | No ablation of novelty |
| R1-5 | Multiple evolved variants — **no mechanism to pick** the right one | No variant router |
| R1-11, R2-7 | Cost = API only; no time/robustness/token/failure-rate reporting | No practical-cost accounting |

Nine complaints, **two root causes**: (A) a fixed human proxy objective the search Goodharts
against; (B) opacity + no distribution. PE2 attacks A with Primitives 1–3, B with Primitives
2 & 4 and the evaluation protocol.

### 1.2 What the code read confirms (so we don't over-defend the current build)

The current implementation is honestly an AlphaEvolve/OpenEvolve re-implementation, and several
of its "novel" parts are inert or mis-wired — Reviewer 2's "novelty is incremental" is
technically accurate about the shipped code:

- **Not a genetic algorithm** — no crossover; every child derives from one parent
  (`program.py`: singular `parent_id`). The "genetic-algorithm-driven" label in `README.md:41`
  is a misnomer; the effective algorithm is a single-parent LLM hill-climber.
- **MAP-Elites descriptors are auto-set to the fitness metrics themselves**
  (`team.py:632–643` takes the first two keys of `fitness_weights`), so the quality-diversity
  grid collapses onto the objective axes — it illuminates nothing orthogonal.
- **Population never pruned / bounded** (`population_size` in `config.py:41` is unused; nothing
  is ever deleted from `database.programs`); the MAP-Elites `accepted` flag is cosmetic.
- **Islands are inert** — parent selection passes `island_id=None`; migration copies only id
  references, never isolates genomes, and is skipped entirely in the parallel path.
- **Cascade evaluation is dead config** (`cascade_evaluation`/`cascade_thresholds` defined,
  serialized, never implemented).
- **Fitness is a single non-stationary scalar** — weighted sum (`function_weight=0.8`,
  `llm_weight=0.2`) with min-max ranges that expand over the run, so scores aren't comparable
  across time; no Pareto, no novelty term.

Strategic implication: we should **not** defend the current machinery as-is. We should present
PE2 as a *new formulation* that (i) makes the previously-inert QD machinery actually do
something (real orthogonal descriptors, bounded archive), and (ii) adds the genuinely novel
minimax + validity + method-gene layer that no baseline has.

---

## 2. Pantheon-Evolve 2 in one picture

```
                    ┌───────────────────────── PE2 co-evolution ─────────────────────────┐
                    │                                                                     │
  method-gene       │   Population 𝒫 of METHODS          Population 𝒜 of FALSIFIERS       │
  LIBRARY  ◄────────┤   (programs)                       (held-out studies / techs /      │
  (typed motifs,    │       │                             metrics / stress transforms /   │
   preconditions,   │       │  propose (policy π)         "trap" datasets)                │
   certificates)    │       ▼                                   │                          │
        ▲           │   child method  ──────────────►  scored on WORST-CASE across 𝒜      │
        │           │       │            fitness(m) = CVaR_χ∈𝒜 [ V-gated held-out score ]  │
   ABSTRACT &       │       │                                   ▲                          │
   CERTIFY  ────────┤       │            fitness(χ) = regret χ exposes on elite methods    │
        ▲           │       ▼                                   │                          │
        │           │   VALIDITY MODEL V  ── flags artifact (overcorrection / boundary     │
  learnable         │   sharpening / imputation-imposed structure) → discounts "fake" gain │
  POLICY π  ◄───────┤                                                                       │
  (cross-run credit)└───────────────────────────────────────────────────────────────────┘
```

**Four primitives** (each maps to reviewer criticisms and to a gap in the three baselines):

1. **Adversarial Evaluator Co-Evolution** — methods vs falsifiers, minimax / distributionally
   robust fitness. *Kills circular eval & overcorrection.*
2. **Mechanistic Method-Gene Library** — every accepted change becomes a transparent, typed,
   reusable motif with a precondition. *Kills the black box; makes evolution cumulative and
   cross-domain.*
3. **Objective Elicitation + Validity Model** — no hand-weighted scalar (Pareto), plus a learned
   real-vs-artifact gate. *Kills human-designed objectives & "beautiful-not-real."*
4. **Learnable Search Policy + real Quality-Diversity** — the strategy is learned across runs;
   fix the inert QD. *Answers reproducibility (raise the floor / shrink variance) & the "not a
   real GA / not ablated" charge.*

Plus a **variant auto-router** (answers R1-5) and an **anti-circular evaluation protocol**
(P1–P6) that is itself the rebuttal.

---

## 3. Primitive 1 — Adversarial Evaluator Co-Evolution (the anti-Goodhart core)

### 3.1 Formalism

Replace the fixed objective `f(m)` with a **two-population minimax game**.

- **Methods** `m ∈ 𝒫` — programs (as today).
- **Falsifiers** `χ ∈ 𝒜` — each a challenge tuple
  `χ = (D_χ, μ_χ, τ_χ)` where `D_χ` is a *held-out* data condition (a study / technology /
  tissue / perturbation the method never trained on, or a **trap** dataset with known structure),
  `μ_χ` is a *metric family* (possibly one the method never optimized against), and `τ_χ` is an
  optional *stress transform* (dropout, batch imbalance, orientation permutation, label shuffle
  on nuisance axes).

**Method fitness** = worst-case (or CVaR at level α) validity-gated score over the current
falsifier ensemble:

```
Fit(m) = CVaR_{χ ~ 𝒜, α} [ V(m, χ) · score(m; D_χ, μ_χ, τ_χ) ]
```

where `V ∈ [0,1]` is the validity model (Primitive 3) that discounts artifactual gains.
CVaR (average of the worst α-fraction) rather than a hard min gives a smoother, less brittle
objective while preserving robustness.

**Falsifier fitness** = the *regret* / generalization gap the falsifier exposes on the current
elite methods `E ⊂ 𝒫`:

```
Fit(χ) = E_{m ∈ E} [ score_in-distribution(m) − V(m,χ)·score(m; D_χ,μ_χ,τ_χ) ]
```

i.e. a falsifier is rewarded for finding a held-out condition where a method that *looked* good
in-distribution actually collapses (or where the validity model catches an artifact).

### 3.2 Why this is Goodhart-resistant (the rigor hook)

This is **population-based distributionally-robust optimization (DRO)** where the uncertainty
set is *learned and expanded by an adversary*, connecting to GAN-style co-evolution and to
open-ended learning (POET / minimal-criterion coevolution).

- Under a **fixed** evaluator, `argmax_m f(m)` is by definition the *best proxy-exploiter* on
  the training distribution — exactly the reviewer's "optimize the benchmark" failure.
- Under the **minimax**, a method can only win by having a *small generalization gap over the
  falsifier-reachable distribution*. If a method improves an in-distribution metric via
  overcorrection, the bio-null trap falsifier (below) drives `Fit(m)` down; if it overfits one
  study, the leave-study-out falsifier does. **The only stable way to win is genuine transfer.**

Proposition (stated plainly for the paper): *for any method `m`, `Fit(m)` upper-bounds `m`'s
worst-case held-out performance over the class of conditions the falsifier population can
realize; therefore a high co-evolutionary fitness certifies bounded generalization gap, whereas
a high fixed-scalar score certifies nothing beyond the training split.*

### 3.3 The falsifier classes (this is where domain expertise enters — as *tests*, not weights)

Instead of the human writing objective *weights*, the human (or the system, ratified by the
validity model) contributes **falsifier generators** — biologically-motivated ways to try to
break a method. Generic classes, instantiated per domain in §7:

- **Leave-condition-out**: hold out a study, a technology (10x vs Smart-seq vs MERFISH), a
  tissue, a donor, a perturbation, a time point.
- **Orthogonal-readout**: score on a biological objective the method never optimized (rare-cell
  recovery, spatial-domain preservation, ligand–receptor program, DE preservation, held-out
  *measured* genes).
- **Trap datasets** — the reviewers' own failure modes weaponized as fitness:
  - *bio-null trap*: batches sharing no real biology → any "mixing" gain is overcorrection.
  - *orientation/artifact trap*: geometry with a permuted axis → any recovered "gradient" that
    survives is an artifact (directly operationalizes R2-11's Cer1-boundary circularity worry).
  - *imputation-null trap*: reference structure withheld → any recovered pattern is imposed.
- **Stress transforms**: subsampling, batch-size imbalance, dropout inflation, label noise on
  nuisance covariates.

Co-evolution means the falsifier population **grows toward whatever currently-elite methods are
weakest at** — the search for counter-examples is automated, not hand-listed.

### 3.4 Mitigating co-evolutionary pathologies (be honest in the paper)

Co-evolution can cycle or "disengage." Standard, citable mitigations we adopt:
- **Falsifier hall-of-fame** + **quality-diversity on falsifiers** (keep a diverse archive of
  hard challenges so the pressure doesn't collapse to one trick).
- **Minimal-criterion coevolution** (POET): a falsifier is admitted only if it is *solvable by
  at least one* current method and *unsolved by the elite* — keeps challenges in the learnable
  band.
- **Gradient of difficulty**: report an explicit "falsifier difficulty ladder" so reviewers see
  the pressure is real and increasing.

---

## 4. Primitive 2 — The Mechanistic Method-Gene Library (anti-black-box, cumulative, cross-domain)

### 4.1 Every accepted mutation ships a falsifiable mechanistic claim

Today the summarizer post-hoc classifies a diff into `{direction, category, is_algorithmic}`.
PE2 requires more: a child is admitted only with a **method-gene** record:

```
MethodGene {
  name:          "adaptive local-density kNN kernel"      # human-readable motif
  code_delta:    <the diff that introduces it>
  claim:         "replaces fixed bandwidth with per-cell density-adaptive bandwidth,
                  reducing over-smoothing in dense manifolds"          # falsifiable
  precondition:  "helps when local density varies > k-fold across the manifold"  # WHEN it helps
  typed_params:  { k: int, alpha: float ∈ [0,1] }
  ablation:      <auto-run A/B: method with vs without this gene on the falsifier set>
  effect:        { held_out_delta: +0.06 ± 0.02, artifact_p: 0.31 }    # measured, certified
}
```

The **ablation is run by the system** (method with vs without the gene, on held-out falsifiers)
so the claim is *tested*, not asserted. This directly answers R1-2h (transparency: the user sees
what changed *and the causal test that it helps and when*) and R2-9 (the analyzer/mutator
contribution is now *demonstrated* per-gene, not asserted).

### 4.2 The library makes evolution cumulative and cross-domain — the real "discovery" claim

Accepted method-genes accumulate into a typed **library / grammar** (DreamCoder-style library
learning). This buys three things no baseline has:

1. **Transparency** — the evolution trace becomes a readable list of named, precondition-tagged
   motifs, not opaque diffs.
2. **Cumulativity** — later runs *compose* known genes instead of re-deriving them (fewer
   evaluations to reach a given robustness → answers cost, R1-11/R2-7).
3. **Cross-domain transfer** — a gene discovered on **batch correction** ("adaptive
   local-density kernel") becomes a *candidate operator* for **3D kernel regression** and
   **gene-panel graph construction**. Demonstrating that a motif discovered in one task
   provably improves a held-out *different* task is a concrete, checkable "method invention"
   result — the thing Reviewer 2 says is missing ("autonomous scientific method invention, not
   guided optimization").

This reframes the deliverable from *"we beat Scanorama on this metric"* to *"we discovered a
transferable, mechanistically-explained, precondition-tagged algorithmic motif and certified
when it helps."* That is a method-development contribution a Cell method-dev reviewer can respect.

---

## 5. Primitive 3 — Objective Elicitation + Validity Model (kill hand-weights; detect eval-hacking)

### 5.1 No scalarization — Pareto front

Replace `f = Σ wᵢ·metricᵢ` with **multi-objective non-dominated sorting** (NSGA-II style) over
the objective vector `(integration, bio-conservation, robustness, cost, …)`. There are **no
hand-tuned weights**, so R2-10's "human-designed objective weights" objection evaporates. The
system reports a **Pareto front** and its trade-off structure; the variant router (§6) picks a
front member for a given dataset. (AutoScientists explicitly lists multi-objective as *future
work* — this is a concrete place we exceed it.)

### 5.2 The validity model V — real signal vs known artifact

`V(m, χ) ∈ [0,1]` is a learned model that predicts whether a metric improvement reflects real
biological signal or a **known artifact class**, trained on data where the answer is knowable:

- **simulations** with known batch/mixing structure (over-mixing is ground-truth detectable);
- **held-out measured genes** (imputation that "predicts" masked measured genes = real; that
  invents structure = artifact);
- **orthogonal assays** (targeted spatial validation, independent modality);
- **negative controls** (permuted axes / null batches).

`V` enters fitness as a **gate/penalty**: a metric gain that `V` flags as overcorrection,
boundary-sharpening, or imputation-imposed structure is *discounted*. This is the
"co-evolution detects reward-hacking / fixes the evaluator" robustness story — and it
operationalizes Reviewer 2's *specific* named failure modes (overcorrection; Silhouette an
"incomplete safeguard"; imputation imposing reference structure; Cer1-boundary circularity) as
first-class, penalized terms. **None of SimpleTES / AutoScientists / DeLM has this** —
SimpleTES names reward-hacking as an *unsolved* limitation; DeLM verifies *evidence support*
(does the claim match its source) but not *biological reality vs artifact*.

### 5.3 On objective autonomy — claim it carefully

Reviewers punished autonomy overclaims. Precise stance: **the system proposes candidate
objective terms and falsifiers; the validity model + human ratify them.** We claim "the human
specifies the *question* and the *sources of validity ground truth*; the system discovers the
*method* and *the conditions under which it holds*." Mark this boundary explicitly (also answers
R1-7 human-vs-agent boundary).

---

## 6. Primitive 4 — Learnable Search Policy + real Quality-Diversity

### 6.1 Learn *how to evolve* across runs

The proposal policy `π` (which method-gene to apply, explore vs exploit, which parent, which
falsifier to target) is **learned across runs** via trajectory-level credit assignment
(SimpleTES-style IRFT/RLVR, or an accumulating ACE-playbook of "edit E in context C yielded
held-out gain G"). This makes the *strategy* the object that generalizes — the "learnable, not
frozen search" thesis — and it is the natural home for the reproducibility answer:

> Over rounds, the learned policy should **raise the median and worst-case** outcome and
> **shrink variance**, not just the best run.

This is exactly the distribution R1-3 asked for, reframed as a *result*: the learned-policy arm
Pareto-dominates the fixed-search arm across seeds, not anecdotally.

### 6.2 Make the quality-diversity real (fix the inert machinery)

- **Behavior descriptors orthogonal to fitness** (the current bug): descriptors = *method-gene
  composition*, *compute cost*, *robustness profile shape across falsifiers* — none of which is
  the objective. Now the MAP-Elites grid illuminates genuinely different *kinds* of method.
- **Bounded, pruned archive** with content-hash dedup (the `content_hash()` that currently
  exists but is unused), so memory and O(N²) bookkeeping stop growing without bound.
- **Replace inert islands** with the method/falsifier predator–prey structure of Primitive 1
  (which *is* a real multi-population model), or fix island genome-isolation if kept.

### 6.3 Reinstate cascade evaluation (dead config → cost lever)

The minimax raises evaluation count (more falsifiers = more evals). Pay for it with the
**cascade evaluation that is currently defined-but-dead**: cheap falsifiers first, promote only
survivors to expensive held-out assays; and adopt SimpleTES's "scale width not depth" and
cheaper open-source generators (gpt-oss beat frontier models in their study). Report the
resulting cost table (§9) — this answers R1-11/R2-7 head-on.

---

## 7. Variant auto-selection (answers R1-5 and "how does the agent contribute")

The Pareto front + validity model + each variant's **falsifier profile** gives a principled
router: given a user's dataset `D_user`, find the falsifier `χ*` whose condition is most similar
to `D_user`, and recommend the front member with the best certified `V·score` on `χ*`. This is a
"which method for which regime" **router**, so the system *navigates between variants* instead of
dumping alternatives on the user (R1-5's exact ask), and it makes the **agentic layer
load-bearing** rather than a wrapper (R1-2 final point, R2-8): the agent's job is *method
selection and certification under the user's regime*, which no fixed pipeline does.

---

## 8. The anti-circular evaluation protocol (P1–P6) — this *is* the rebuttal

Ship the redesign with a protocol that structurally cannot be accused of circularity. State it
once, apply it to all four domains.

- **P1 — Objective/report disjointness.** Metrics used *inside* the evolution objective are
  disjoint from metrics *reported*. Enforced and logged. (Answers R1-2f, R2-8.)
- **P2 — Leave-condition-out generalization.** Evolve on `C_train`; report only on held-out
  studies/technologies/tissues/perturbations `C_test` never seen in the loop. Splitting the
  *same* cells is banned. (Answers R2-10, R1-min1.)
- **P3 — Adversarial validity.** Every headline gain must survive the falsifier ensemble + `V`;
  report an **artifact-rejection p-value** per claim. (Answers "beautiful not real", R1-2g.)
- **P4 — Right counterfactual.** Baseline arms under *identical* harness / budget / model
  backbone: (a) **expert human, one week**; (b) **Claude Code**; (c) **AlphaEvolve / OpenEvolve
  / AIDE** (the code-evolution systems R2-6 named). "Beyond human baseline" replaces
  "super-human" (R1-4, R2-10). (Answers R1-4, R2-6, R2-10.)
- **P5 — Distributional reporting.** ≥ N seeds; report **median / worst / IQR** and the
  **failure rate** (fraction of runs producing ≤ the input method), with the **dashed baseline
  line** the reviewer literally asked for. (Answers R1-3.)
- **P6 — Certificate.** Each shipped method carries `{mechanistic claim, held-out interval,
  artifact p-value, precondition}` — a single reviewable object. (Answers R1-2h.)

---

## 9. Application to the four target tasks

For each: *what evolves*, *falsifier design*, *validity/trap*, *held-out claim*, *counterfactual*.
The template is identical (that uniformity is itself a contribution — one robust method-discovery
algorithm, four domains), which is what makes the platform claim credible rather than a grab-bag.

### 9.1 Batch correction / data integration (flagship — highest-leverage rebuttal)

- **Evolves:** the integration algorithm (Scanorama / Harmony / BBKNN and *de novo* variants).
- **Falsifiers:** leave-one-study-out, leave-one-technology-out; **bio-null trap** (batches with
  no shared biology → mixing gain = overcorrection); orthogonal held-out biology (rare-state
  recovery, DE preservation).
- **Validity:** `V` penalizes iLISI/kBET mixing gains that co-occur with loss of held-out
  biological signal (directly answers "mixing improvable by overcorrection; Silhouette
  incomplete safeguard").
- **Held-out claim:** improved integration on **studies/technologies never in the loop**, not a
  same-cells split (answers R2-10 head-on).
- **Counterfactual:** expert-week + Claude Code + AlphaEvolve/OpenEvolve/AIDE, identical budget;
  distribution over ≥10 seeds with a dashed "original Scanorama" baseline (answers R1-3, R1-4).
- **Also resolves R1-3's asymmetry:** Harmony/BBKNN being "more modest" becomes a *result*
  (their robustness ceiling is already near the certified front — the certificate shows *why*),
  not an embarrassment.

### 9.2 Gene panel design

- **Evolves:** the *selection algorithm* (not "run a fixed RL"). Method-genes: consensus-fill
  rule, adaptive panel-size, Pareto-vs-RL routing.
- **Falsifiers:** held-out tissue/technology + **orthogonal objectives the panel never
  optimized** — spatial-domain preservation, rare-cell-state detection, ligand–receptor program
  recovery (exactly R2-8's list), ideally against a real targeted assay.
- **Anti-circular:** objective uses ARI-family on `C_train`; **report** on the orthogonal
  held-out biology (answers R1-2f/R2-8 "circular ARI").
- **Agentic contribution made concrete:** the agent *discovers the selection rule and its
  precondition* and *routes* between RL/Pareto/consensus by regime — answering R1-1's "the
  multi-agent system only seeds a list" and R1-5's "which variant."

### 9.3 3D reconstruction (embryo spatial landscape)

- **Evolves:** the reconstruction / registration / imputation algorithm.
- **Falsifiers/traps:** **orientation-artifact trap** — withhold the Cer1-defined boundary /
  permute embryo orientation and test whether the recovered "biological gradient" survives;
  **cross-embryo reproducibility** — must hold across all 6 embryos, not one representative.
- **Validity:** `V` penalizes gradients explainable by smoothing/orientation artifact — this
  literally turns Reviewer 2's circularity objection (R2-11) into the fitness function.
- **Held-out claim:** the recovered proximal–distal axis is certified *not* an orientation/
  reconstruction artifact, reproducibly across embryos.

### 9.4 Virtual-cell modeling downstream task

- **Evolves:** the downstream method (perturbation-effect prediction / GRN inference / model
  routing).
- **Falsifiers:** unseen perturbations + unseen cell lines (e.g. Essential Perturb-seq transfer
  K562/RPE1 → HepG2/Jurkat); held-out *measured* genes; orthogonal assays.
- **Fixes vcRouter "tautology" (R2-14):** the router's ground truth becomes **real downstream
  task performance on held-out biology**, not registry metadata — so routing accuracy measures
  "did it pick the model that actually performs best here," not "can it retrieve metadata."
- **Held-out claim:** predicted perturbation effects validated on measured held-out genes /
  orthogonal assay, with uncertainty (answers R2-15 "speculative overlay").

---

## 10. Positioning vs the three baselines (+ the code-evolution family)

| System | Search unit | Objective | Evaluator | Learned policy | Multi-obj | Validity / artifact | Transparency |
|---|---|---|---|---|---|---|---|
| **AlphaEvolve / OpenEvolve / AIDE** | program (single pop) | fixed scalar | trusted static | no | no | no | opaque diffs |
| **SimpleTES** | proposal loop (C,L,K) | task score | static (names **reward-hacking as its limit**) | **yes** (IRFT) | no | no | trajectory only |
| **AutoScientists** | self-org team | single metric | 2σ noise-gate + seed re-run | no (reactive) | **future work** | no (only significance) | report/model-card |
| **DeLM** | shared-context MAS | task score | **admission-time evidence check** | no | no | evidence-support only, **no evolution** | verified gists |
| **PE2 (ours)** | method **vs falsifier** (minimax) | **Pareto, no weights** | **co-evolving adversary + validity gate** | **yes (cross-run)** | **yes** | **yes — real-vs-artifact, the central novelty** | **method-gene library + certificate** |

Reading of the table for the rebuttal: PE2 is not "one more evolve loop." It occupies the
**exact open gap** the field's own 2026 papers name but do not fill — *robustness to evaluator
infidelity in a domain (wet-lab biology) where the evaluator is provably imperfect.* We can
**adopt** the baselines' strong parts as engineering (SimpleTES width-scaling + IRFT credit;
AutoScientists' 2σ seed-gate as a *special case* of our validity gate; DeLM's admission-time
verification as a *special case* of our certificate) and still have a distinct core contribution.

---

## 11. Ablations — answer R2-9 directly, and make "we win" mean "we generalize + don't cheat"

Headline metrics are **held-out generalization gap** and **artifact-rejection rate**, *not*
in-distribution score. The anti-Goodhart ladder is the key ablation:

1. fixed scalar (current PE)  →
2. + Pareto (no weights)  →
3. + validity gate `V`  →
4. + adversarial falsifier co-evolution  →
5. + method-gene library (cumulative/transfer)  →
6. + learnable cross-run policy.

Each rung should **lower the held-out gap and the artifact rate** even if it *lowers or leaves
flat the in-distribution score* — that inversion is the whole point and is itself the figure
that refutes "you just optimized the benchmark." Plus per-component on/off (analyzer, mutator,
QD-with-orthogonal-descriptors, cascade) and the external arms (AlphaEvolve/OpenEvolve/AIDE/
Claude Code/human-week) under identical harness.

---

## 12. Core loop (pseudocode)

```python
def pe2_evolve(seed_methods, falsifier_generators, V, policy, budget):
    P = Archive(descriptors=[gene_composition, cost, robustness_profile])  # real orthogonal QD
    A = FalsifierArchive(quality_diversity=True, hall_of_fame=True)        # POET min-criterion
    library = MethodGeneLibrary()
    P.add_all(seed_methods); A.seed(falsifier_generators)

    while budget.remaining():
        # ---- METHOD move ----
        parent, inspirations = policy.select_parent(P, library)
        gene = policy.propose_gene(parent, library, mode=policy.explore_exploit())
        child = mutate(parent, gene)                     # analyzer→mutator, but gene-typed
        # cascade: cheap falsifiers first, promote survivors
        scores = cascade_eval(child, A, V)               # V-gated, held-out only
        fit = cvar(scores, alpha)                        # distributionally-robust fitness
        if child.is_new(content_hash):                   # dedup
            cert = certify(child, gene, A, V)            # runs the A/B ablation → certificate
            if cert.artifact_p > THRESH and cert.held_out_delta > 0:
                library.add(gene, cert)                  # cumulative, transparent
            P.insert(child, fit, cert)

        # ---- FALSIFIER move (co-evolution) ----
        if step % k == 0:
            chi = policy.propose_falsifier(A, elite=P.elite())   # target elite weaknesses
            if minimal_criterion(chi, P):                # solvable-by-some, unsolved-by-elite
                A.insert(chi, regret=chi.regret(P.elite(), V))

        policy.credit(trajectory)                        # cross-run trajectory-level learning

    front = P.pareto_front()                             # no scalarization
    return front, library, router_from(front, A, V)      # variant router for §7
```

---

## 13. Reviewer-comment → design-feature traceability (the money table for the response letter)

| Reviewer comment | PE2 feature that answers it |
|---|---|
| R1-2f / R2-8 circular eval (objective = report metric) | P1 disjointness + Primitive 1 (fitness = held-out worst-case, never the reported metric) |
| R1-2g "beautiful not real" | Primitive 3 validity gate + P3 artifact-rejection p-value |
| R1-2h black box | Primitive 2 method-gene certificate + online diff/claim viewer |
| R1-3 no variance / failure distribution | P5 distributional reporting + Primitive 4 (policy raises floor/shrinks variance) |
| R1-3 Harmony/BBKNN "more modest" | certificate explains the robustness ceiling (a result, not an omission) |
| R1-4 / R2-10 wrong super-human counterfactual | P4 human-week + Claude Code + AlphaEvolve/OpenEvolve/AIDE arms; "beyond human baseline" wording |
| R1-5 which variant to pick | §7 variant router (regime-matched, certified) |
| R1-min1 cross-dataset generalizability | P2 leave-condition-out (this becomes the headline design, not a caveat) |
| R1-min2 optimize vs explore modes | recast as explicit policy modes in Primitive 4; documented |
| R2-9 novelty not ablated | §11 anti-Goodhart ablation ladder (held-out gap + artifact rate) |
| R2-10 human-designed objective weights | Primitive 3 Pareto (no weights) |
| R2-10 same-cells split ≠ generalization | P2 (banned) |
| R2-10 overcorrection / Silhouette incomplete | Primitive 3 `V` penalizes it explicitly |
| R2-11 embryo circularity (Cer1 boundary) | §9.3 orientation-artifact trap falsifier + `V` |
| R2-14 vcRouter tautology | §9.4 ground truth = real held-out downstream performance |
| R1-11 / R2-7 cost / time / robustness | §6.3 cascade + width-scaling + §9 cost table |
| R1-7 / R2-11 human-vs-agent boundary | §5.3 explicit boundary; certificate records human inputs |

Every reviewer criticism of Pantheon-Evolve maps to a *designed feature*, not a promise — which
is the strongest possible shape for a response letter.

---

## 14. Honest limitations (state them; reviewers reward candor)

- **Validity-model fidelity is itself a bound.** `V` can be wrong; we mitigate with multiple
  ground-truth sources (sims + held-out measured genes + orthogonal assays) and report `V`'s own
  validation. But `V` is *strictly better than a fixed scalar* — it can only add rejection power.
- **Co-evolution can cycle/disengage** — mitigated by hall-of-fame + QD-on-falsifiers +
  minimal-criterion admission (§3.4); report the falsifier difficulty ladder as evidence.
- **Higher evaluation cost** than single-population evolution — paid down by cascade (§6.3),
  cheaper generators, and cumulative reuse from the library; reported transparently.
- **Autonomy is bounded and stated** (§5.3): human supplies question + validity sources; system
  discovers method + preconditions. We will *not* re-use "autonomous discovery"/"singularity"
  framing (also fixes R2-2, the Discussion tone).

---

## 15. Phased implementation plan (grounded in the current repo)

Reuse the existing `pantheon/evolution/` scaffold; the changes are surgical where possible.

- **M0 — De-risk the two falsifiers first (Weeks 1–3).** On batch correction only:
  (a) implement P1/P2 harness (leave-study-out; objective/report disjointness) and re-run the
  *existing* Scanorama evolution under it — this alone tells us how much of the current headline
  survives an honest split (critical to know before the rebuttal). (b) Implement one trap
  (bio-null) + a v0 validity gate. Deliverable: the anti-Goodhart ladder rungs 1→3 on one task.
- **M1 — Minimax + certificate (Weeks 3–6).** Add the falsifier archive, CVaR fitness,
  method-gene records + auto-ablation certificate. Wire real orthogonal QD descriptors; bound &
  dedup the archive; reinstate cascade. `config.py`: retire dead config, add falsifier/validity
  knobs. Deliverable: ladder rungs 4–5 on batch correction, with distribution over seeds (R1-3).
- **M2 — Learnable policy + variant router (Weeks 6–9).** Cross-run trajectory credit; regime
  router (§7). Deliverable: ladder rung 6; learned-policy arm Pareto-dominates fixed-search arm
  across seeds.
- **M3 — Port the template to the other three tasks (Weeks 9–14).** Gene panel, 3D
  reconstruction, virtual-cell downstream — each is *just* new falsifier generators + a `V`
  head; the loop is unchanged (that uniformity is the platform argument). Cross-domain
  method-gene transfer experiment (§4.2) is the flagship "method invention" figure.
- **Throughout — counterfactual arms (P4):** run Claude Code + AlphaEvolve/OpenEvolve/AIDE +
  an expert-week arm under the identical harness. This is the single most persuasive addition for
  both reviewers and is mostly harness/plumbing, not new algorithm.

**Contamination guardrail (from `benchmarks/README.md`):** tasks used to *evolve/tune* must be
disjoint from tasks *reported*; use public held-out/private splits; OmicBench is in-house →
dev/supplementary only, never the neutrality argument.
