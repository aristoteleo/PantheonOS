---
id: developmental_gene_panel
name: Developmental Gene Panel Design Workflow
description: |
  Panel design for DEVELOPING / dynamic systems (embryonic organs, differentiation, regeneration).
  The target experiment is usually a LATE / terminal stage, but the biology is a trajectory: terminal
  cell types are end-products of earlier lineage programs. A panel built from the target stage alone
  resolves terminal STATES but systematically misses the developmental REGULATORS that produced them.
  This workflow therefore uses TWO references — the target-stage dataset (cell-state resolution) and an
  independent EARLIER-stage reference (developmental origin) — combines them under an explicit budget
  split, and benchmarks on the target stage.
tags: [gene-panel, development, trajectory, pseudotime, RNA-velocity, MERFISH, spatial, scrna-seq]
---

# Developmental Gene Panel Design

Use this skill INSTEAD of `gene_panel_selection` when the system is **developmental / dynamic**:
embryonic organs, in-vitro differentiation, regeneration, or any design where the user cares about
**lineage origin and regulators**, not only terminal cell-type classification.

## Why a separate workflow (the motivating evidence)

In a mouse embryonic-heart case study, a known cardiac developmental co-repressor (a CHD-associated
gene with published human evidence) was ranked by six unbiased methods on both a late (E10.5–E14.5)
and an early (E6.5–E8.5) reference:

| ranking method | early reference | late (target) reference |
|---|---|---|
| cell-type DE (Wilcoxon) | **top 0.6%** | top 3.0% |
| trajectory pseudotime (DPT, Pearson) | **top 1.1%** | top 79% |
| trajectory pseudotime (DPT, Spearman) | **top 1.8%** | — |
| cross-stage ANOVA (within lineage) | **top 1.6%** | top 18.6% |
| global marker rank | — | top 21.6% |

**Every method recovered the gene from early data and failed on the target-stage data.** The reason is
biological: the gene rises ~19-fold along the differentiation trajectory, then becomes broadly expressed
and static once cells are differentiated. Literature lookup did not rescue it either — theme-level
queries ("cardiac transcription factors") return only canonical genes.

**Conclusion driving this skill:** developmental regulators must be recovered from an EARLY reference by
TRAJECTORY-AWARE ranking. Neither target-stage DE nor literature search is sufficient.

## Workflow enforcement

Steps 1–6 are mandatory and ordered. Do not silently skip a step; if a step is impossible (e.g. no early
reference exists), say so explicitly in the report and justify the fallback.

## Workdir / agents / reporting
Work in the workdir given by the leader. If sub-agents exist, delegate execution to `analysis_expert`
and interpretation to `biologist`; otherwise run the analysis yourself with the notebook/python toolset.
Write `report_analysis.md` (Summary / Data / Methods / Results / Key findings / Limitations).

---

# Workflow

## 1) Two references and the lineage map

**1.1 Target dataset** — the stage you will actually assay (e.g. E10.5–E14.5 heart for MERFISH).
Record: cells, genes, cell types, timepoints, batches, and any train/test split.

**1.2 Early developmental reference** — an independent dataset covering the stages where the target's
cell types are SPECIFIED (e.g. a gastrulation/organogenesis atlas). If the user does not supply one,
search CELLxGENE Census / published atlases for the same tissue at earlier stages, and state the choice.

**1.3 Lineage map (write it down explicitly).** For each target cell type, name the early lineage/
progenitor state it derives from. Example (heart):

```
target: ventricular CM, atrial CM   <-  early: Cardiomyocytes, Pharyngeal (cardiopharyngeal) mesoderm
target: epicardial                  <-  early: (pro)epicardial / mesothelial progenitors
target: endocardial/vascular EC     <-  early: haematoendothelial progenitors
```

Only lineages relevant to the target organ are carried forward. **This is the step that prevents
budget dilution** (see 3.1).

**1.4 Preprocessing (identical for both):**
`filter_genes(min_cells=10)` → `normalize_total(1e4)` → `log1p`.
Downsample if large, PRESERVING all cell types. Split the TARGET data into train/test.

## 2) Terminal-state component (from the target dataset)

Standard algorithmic selection on the target stage — this is what gives cell-state resolution in the
actual experiment.

- per-cell-type DE (`sc.tl.rank_genes_groups`, Wilcoxon) and/or HVG; optionally scGeneFit/SpaPROS.
- call each method ONCE with full ranked output, then slice top-K in pandas.
- determine an optimal seed size by an ARI/accuracy vs K sweep on the train split.

## 3) Developmental-origin component (from the early reference) — THE CORE OF THIS SKILL

### 3.1 Grouping granularity (CRITICAL — do this before ranking)

Whole-embryo atlases have many cell types, most irrelevant to the target organ. Equal budget across
all of them starves the organ of interest.

- **Group fine cell types into coarse LINEAGES** (typically 5–10), one of which is the target organ's
  lineage. Merge the organ's sub-states into a single lineage group.
- Document the mapping in the report.

**Define the organ lineage STRICTLY.** The organ-lineage group must contain only states that are
*bona fide* progenitors or products of the target organ:

- **INCLUDE**: the organ's differentiated cells and its committed/organ-specified progenitors
  (e.g. for heart: Cardiomyocytes + cardiopharyngeal/Pharyngeal mesoderm).
- **EXCLUDE**: (i) extraembryonic tissues (e.g. ExE mesoderm — yolk sac/allantois, contributes no
  cardiomyocytes); (ii) generic, heterogeneous or unassigned compartments (e.g. "Mesenchyme");
  (iii) multipotent early states that feed many organs (Nascent/Mixed mesoderm, Primitive Streak,
  Epiblast).
- **Upstream progenitors still belong on the TRAJECTORY** (Step 3.2 needs the full path
  progenitor → terminal to define pseudotime) — but they must NOT be merged into the organ group used
  for the **lineage DE contrast** in Step 3.3.

> **Why this matters (measured):** in the case study, merging Nascent/ExE/Mixed mesoderm and generic
> Mesenchyme into the "cardiogenic" group diluted the organ-specific signal and pushed a genuine cardiac
> regulator from lineage-DE rank **#136 to #271**, dropping it out of the selected set. A too-permissive
> lineage definition is the most common failure mode of this step.

> Case-study evidence: with 34 fine whole-embryo types, the target regulator required a **1,928-gene**
> panel to be included; after grouping into 7 lineages it entered at **742 genes** — grouping both
> concentrates the budget (~29 → ~142 genes/group) and sharpens the rank (#163 → #136), because pooling
> the organ's sub-states strengthens its shared signal.

### 3.2 Trajectory construction

Restrict to the cells forming a coherent differentiation path to the target organ (progenitor →
intermediate → terminal). **Do not include unrelated cell types** — a trajectory over non-connected
populations is meaningless.

```python
sc.pp.highly_variable_genes(b, n_top_genes=2000); sc.pp.scale(b, max_value=10)
sc.pp.pca(b, n_comps=30); sc.pp.neighbors(b, n_neighbors=15)
sc.tl.diffmap(b)
# root MUST be the least-differentiated state, constrained by cell type
root = np.where(b.obs['celltype'].astype(str) == '<progenitor type>')[0]
b.uns['iroot'] = int(root[np.argmin(b.obsm['X_diffmap'][root, 1])])
sc.tl.dpt(b)
pt = b.obs['dpt_pseudotime'].values
ok = np.isfinite(pt)          # MANDATORY: DPT emits inf on disconnected graphs
```

**Two mandatory guards** (both were real failure modes in the case study):
1. **root constrained by cell type** — not merely "earliest timepoint";
2. **`isfinite` filter** — a single `inf` contaminates the mean and corrupts every gene's correlation.

If `spliced`/`unspliced` layers exist, additionally compute **scVelo latent time** as an independent
trajectory estimate (different signal source: splicing dynamics) and use it as confirmation.

### 3.3 Rank genes by developmental dynamics (use ALL of these)

**The rankings fall on exactly TWO independent axes — do not double-count them.**

| axis | ranking | metric | note |
|---|---|---|---|
| **identity** | lineage DE | Wilcoxon z, target lineage vs rest | which genes mark the lineage |
| **dynamics** | pseudotime correlation | Pearson \|r\| of log-norm expression vs pseudotime | primary; catches monotone trends |
| **dynamics** | pseudotime correlation (rank) | Spearman \|ρ\| | *robustness check on the same signal* — report it, but do NOT count it as a separate vote (it is diluted by dropout ties) |

> **Do NOT use a cross-stage ANOVA (F across developmental stages) as a selection metric.** It was
> evaluated and rejected for three measured reasons: (i) **redundant** — pseudotime already orders cells
> continuously, and stage explained 85% of pseudotime variance; (ii) **batch-confounded** — in atlases,
> `stage` and `sequencing.batch` are typically collected together (in the case study E7.25 was almost
> entirely one batch), so a cross-stage F partly ranks genes by batch, re-introducing exactly the
> confound Step 3.4 removes; (iii) **it selects the wrong gene class** — empirically its top hits are
> embryonic haemoglobins (Hbb-y, Hba-x, Hbb-bh1), Hmga2 and ribosomal/housekeeping genes, i.e. maturation
> "clock" signal rather than lineage regulators. Report it as a descriptive statistic if useful, never as
> a selection vote.

```python
ptc = pt[ok] - pt[ok].mean(); Xc = X[ok] - X[ok].mean(0)
corr = (Xc * ptc[:, None]).sum(0) / (np.sqrt((Xc**2).sum(0)) * np.sqrt((ptc**2).sum()) + 1e-9)
```

> **Rank the full gene list — do not truncate to a preset N before voting.** A cutoff smaller than the
> eventual anchor count throws away candidates *before* voting, so genes that sit consistently just
> outside the cut in every ranking can never accumulate votes. Derive the cutoff afterwards (3.3a).

### 3.3a Derive the anchor budget from the data — DO NOT assume a number

The number of anchors is a **result to be measured, not a parameter to be chosen**. Picking a round
number (200, 300) and then ranking to fill it makes every downstream claim ("gene X made the panel")
an artefact of that choice. Run all three criteria below and cross-check them.

**Criterion 1 — effect size, not significance.** Sweep an effect-size threshold and report the
(threshold → gene count) curve. Report the count at several thresholds; do not report only one.

> **Significance alone is useless at atlas scale — verify this before relying on it.** In the case
> study (89,267 cells) **5,354 genes passed FDR<0.05 and 4,713 passed FDR<0.01**. With that many cells
> almost everything is significant, so an FDR cut carries no selection power. Adding an effect-size
> floor restores it: FDR<0.01 **and** |log2FC|≥1.5 → 356 genes; ≥2.0 → 244; ≥2.5 → 186.

**Criterion 2 — elbow of the ranked statistic** (assumption-free, purely geometric): the rank at which
the sorted score curve departs furthest from the chord joining its endpoints.

```python
z = np.sort(scores)[::-1][:3000]; x = np.arange(len(z))
x1, y1, x2, y2 = 0, z[0], len(z) - 1, z[-1]
d = np.abs((y2-y1)*x - (x2-x1)*z + x2*y1 - y2*x1) / np.hypot(y2-y1, x2-x1)
elbow = int(np.argmax(d))
```

**Criterion 3 — marginal cost against the downstream objective.** Using the evaluator, sweep the anchor
count and record the achievable target-stage quality Q. The knee is where Q starts collapsing — anchors
consume the optical budget, so beyond some count they are no longer free.

**The committed set is anchors ∪ disease genes — sweep THAT, not the anchors alone.** Criterion 3
must be run on the union, because the union is what occupies slots. Report the committed fraction
`|anchors ∪ disease| / panel_size`, and check that the remaining slots can still reach the
cell-state-quality floor. In the case study the two components were derived independently and
overlapped by only 30 genes, so 613 anchors + 207 disease genes committed **79% of a 1000-gene
panel** and left 210 slots for discrimination — the optimiser then had almost nothing to optimise.

**Prefer multi-role genes when ranking WITHIN the qualified sets.** Once each axis has its
evidence-based qualified set, order candidates so that a gene belonging to more than one set
(developmental anchor *and* disease gene *and* state marker) is taken first. It costs one slot and
scores on several objectives, which is the only way to keep the committed fraction down without
lowering any evidence threshold.

**Cross-check, then commit.** If the three agree within roughly a factor of two, take the consensus
range. **If they disagree, report the disagreement — do not silently pick one.** In the case study
criteria 1 and 2 landed at **356** and **349** (independent methods, striking agreement) while
criterion 3 gave **150–200** (it measures a different thing: what the optical budget can afford).

Apply the same procedure **per ranking axis** — class A and class B (3.3b) each get their own elbow.
Do not impose a fixed A/B ratio; let the two cutoffs set the split and report it.

### 3.3b Two classes of developmental gene — allocate the anchor budget to BOTH

A developmental program contains two distinct gene classes, and **they require different metrics**:

| class | biology | behaviour along the trajectory | metric that finds it |
|---|---|---|---|
| **(A) Trajectory-dynamic** | differentiation effectors: sarcomere/structural genes, stage-specific ligands, commitment factors | expression *changes* strongly along pseudotime | pseudotime correlation (dynamics axis) |
| **(B) Lineage-identity regulators** | TFs, co-activators/**co-repressors**, chromatin factors that *specify and maintain* lineage identity | lineage-restricted but **temporally FLAT within the lineage** | **lineage DE only** — temporal metrics actively reject them |

**A naive multi-metric consensus systematically loses class (B).** Requiring agreement across both
lineage and temporal metrics demands a gene be simultaneously lineage-specific *and* time-varying;
identity regulators are lineage-specific and time-**invariant** by definition, so they can never reach
the vote threshold.

> **Measured in the case study:** a genuine cardiac co-repressor scored lineage-DE **#152** (strongly
> lineage-specific) but was flat along the dynamics axis (it is an identity gene,
> not a clock gene). Under a "≥3 of 4 metrics" consensus it sat exactly on the boundary and its
> selection flipped with the random subsampling seed — i.e. not robustly recoverable.

**Therefore build BOTH classes, each with its own data-derived cutoff** (3.3a), and report the split
that results — do not impose a ratio in advance:

- **Class A anchors**: multi-metric consensus as in 3.3, cut at that axis's own elbow.
- **Class B anchors**: rank by **lineage DE alone**, *independently* of any temporal metric, cut at that
  axis's own elbow. Optionally prioritise annotated **transcriptional / chromatin regulators** within
  this class, since the goal of the developmental component is regulatory coverage — state clearly if
  such an annotation filter is applied.
- Deduplicate across the two classes; report each anchor's class and its evidence.

**Consensus (class A):** take the union/intersection of the 3.3 rankings (state which).

If the two cutoffs together exceed what the panel can carry, **rank within the qualified set** rather
than lowering the threshold — the qualifying step stays evidence-based, and the report states plainly
how many qualified vs.\ how many fit.

### 3.4 Robustness: confounding check (MANDATORY)

Early atlases usually confound `stage` with `batch`. Quantify before trusting the ranking:

- **variance decomposition** of pseudotime by `cell type` vs `stage` vs `batch`;
- **within-cell-type control**: for the selected genes, test variation across stages *inside one cell
  type*. If a gene's signal were a batch artefact it would vary here (stage ⟂̸ batch); if it is a cell-
  identity gene it will be flat.

> Case study: cell type explained 94.8% of pseudotime variance vs 7.2% for batch, and the selected
> regulator was flat within cell type (F≈1) — i.e. identity-driven, not batch-driven.

## 3c) Disease / phenotype gene coverage

A developmental panel is usually built to study a disease or phenotype, not just cell types. If the
user supplies (or the literature defines) a curated **disease gene set** for the organ — e.g.
congenital-heart-disease (CHD) genes for the heart, or a phenotype/GWAS gene list — treat its coverage
as an explicit design target alongside the developmental component.

- Load the set, map to the target species' symbols, and intersect with the TARGET dataset.
- **Apply the same expression filter as Step 4**: a disease gene not expressed in the target tissue is
  an unusable probe; drop it and record which ones.
- **The disease-gene budget is what survives those two filters — do not pick a round number.** The set
  is externally curated, so its size is a fact, not a design choice. Report `|expressed disease set|`
  and carry all of it if it fits. Only if it does not fit does a ranking step apply, and then rank by
  multi-role value (below), stating how many qualified vs.\ how many fit.
- Report coverage as `|panel ∩ disease set| / |expressed disease set|`, and prefer disease genes that
  ALSO serve another role (cell-type marker, developmental regulator, pathway member) — they cost one
  slot but satisfy two objectives.

> Do not let disease-gene stuffing crowd out the discrimination backbone: coverage is only useful if
> the panel still resolves the cell states (Step 5 benchmark). Add them, then re-check identifiability.

## 4) Budget allocation and merge

- **Derive the split, do not decree it.** The developmental-anchor count comes from 3.3a (data-derived
  cutoffs), the disease-gene count from 3c (what survives curation + expression filtering); terminal-state
  markers take the remainder. Report the resulting split and the evidence behind each number. A split
  asserted up front ("300 anchors, 200 disease genes") makes every later claim about which genes made the
  panel an artefact of that assertion.
- Note the components OVERLAP (a gene can be all three) — exploit that: prefer multi-role genes so one
  slot serves several objectives. Because of overlap the three counts sum to more than the budget; report
  both the per-role counts and the number of multi-role genes.
- Genes selected in Step 3 are **ANCHORS**: they are held FIXED in any downstream optimisation
  (evolutionary or greedy), because they are not recoverable from the target-stage data.
- **Filter anchors by TARGET-tissue expression (MANDATORY).** Existence in `var_names` is not enough:
  the probe is spent in the target tissue, so an anchor that is not actually *expressed* there is a
  wasted slot. Require **detection in >=5% of target cells** (tune the threshold to the assay); drop
  anchors below it and backfill from the next-ranked developmental candidates that do pass.

  ```python
  pct = (target.X > 0).mean(0)                     # per-gene detection rate in the TARGET data
  ok  = [g for g in anchors if g in tgi and pct[tgi[g]] >= 0.05]
  # backfill from the ranked developmental candidate list until len(ok) == anchor budget
  ```

  > **Exception — markers of RARE cell types.** A single GLOBAL detection floor silently kills the
  > diagnostic genes of small populations: a marker expressed in 90% of a population that is 3% of the
  > tissue has ~3% global detection and fails the filter, even though it is precisely the probe the
  > experiment needs. Qualify a gene if it is detected globally at the floor **OR** detected in
  > >=30% of cells inside at least one cell state (ignore states with <30 cells — too noisy). In the
  > case study this rescued `Hcn4` (sinoatrial pacemaker; 4.7% global, 30% within-state), `Kel` and
  > `Pdzk1ip1`, while correctly still excluding `Irx1`/`Irx2` (~20% even within their best state).
  > Apply the same rule to the evaluator's detection gate, or the two will disagree and valid panels
  > will be rejected.

  > Report how many anchors were dropped and why. Typically these are genuinely early-restricted
  > regulators that switch off after organogenesis (in the case study: Phlda2 68%->1.1%, Cfc1
  > 37%->0.3%, Foxf1 28%->0.2%, Isl1 22%->1.4%). **Exception:** keep such a gene deliberately — and say
  > so — if the study needs to detect its *re-activation* (e.g. de-differentiation after a perturbation).
- **Filter anchors by OPTICAL COST (MANDATORY for imaging-based assays).** Detection rate says whether
  a probe is readable; it says nothing about what the probe *costs*. In an imaging assay the binding
  resource is total transcript density, and expression is distributed with a long tail — a handful of
  bright genes can consume the entire budget. Two filters, in order:

  **(i) Drop non-informative high-abundance classes outright.** Mitochondrial, ribosomal, haemoglobin,
  and lncRNA/predicted-gene families are high-abundance and carry no lineage information. A DE ranking
  will surface them (they are genuinely differential between lineages), so they must be excluded by
  name, not by rank.

  ```python
  NOISE_RE  = re.compile(r'^(mt-|Rpl|Rps|Mrpl|Mrps|Hb[ab]-|Hbb|Hba)', re.I)
  NOISE_SET = {'Actb','Eef1a1','Fau','Gapdh','Hsp90ab1','Hspa8','Malat1','Ppia','Ptma',
               'Tmsb4x','Uba52'}          # classic housekeeping — not matchable by prefix
  drop = lambda g: bool(NOISE_RE.match(g)) or g in NOISE_SET
  ```

  Do **not** blanket-exclude uncharacterised genes (`*Rik`, `Gm*`): many are real lineage-restricted
  transcripts. Let filter (ii) price them instead — an uncharacterised gene that is cheap costs
  nothing to carry, and an expensive one is removed on cost, not on its name.

  **(ii) Cap PER-GENE cost — not the panel total.** The binding optical constraint is the brightness
  of an individual gene, which is what vendor design portals flag ("genes whose abundance may be too
  high and could cause optical crowding artifacts"). Express each gene's cost as its share of a cell's
  transcripts (`mean raw counts / median total counts per cell`) and drop anchors above the ceiling.

  > **Calibrate the ceiling against real data from the assay — do not invent one.** Measured on a real
  > 161-plex MERFISH dataset: median 263 transcripts/cell, and the panel's BRIGHTEST gene is
  > 4.8 transcripts/cell = **1.8% of the cellular total**. That is the empirical per-gene ceiling.

  > **Do NOT impose a cap on the panel's TOTAL load at this plex.** Published numbers: MERFISH optical
  > crowding sets in at ~3-4 molecules/um^2 = ~70,000-100,000 molecules/cell (50% efficiency loss),
  > while a real 815-plex MERSCOPE panel detects ~727 counts/cell — about **1% of the ceiling**. A
  > total-load cap at this plex constrains nothing physical, and it actively hurts: bright genes are
  > the discriminative markers, so capping the sum forces the optimiser to discard them. In the case
  > study an invented 25% total cap cost **0.019 of cell-state quality Q for no physical reason**, and
  > was the main reason the quality floor looked unreachable.

  > **Filter (i) already fixes the load.** After excluding the noise classes, a purpose-built
  > classification panel in the case study sat at 20% of cellular mRNA unaided — the 46% seen before
  > filtering was entirely haemoglobin, ribosomal and mitochondrial genes, not legitimate markers.

  > Report the panel's total load as a DIAGNOSTIC, and state the per-gene ceiling you used and what
  > calibrated it. Validate the final panel in the vendor's design portal before synthesis.

- **Validate the finished panel mechanically, and iterate until it passes.** Checking membership in a
  list is not a judgement call and must not be left to narration: in three consecutive case-study runs
  the agent accurately described the eligibility list in its report and still shipped haemoglobin and
  ribosomal genes in the panel. If the workdir provides a validator (e.g. `validate_panel.py`), RUN IT
  and fix exactly what it names before writing the deliverables. Verify: exact panel size, no
  duplicates, every gene in the eligible list.
- Deduplicate; verify every remaining gene exists in the TARGET dataset's `var_names`.
- Final size must equal N exactly.

## 5) Benchmarking (on the TARGET stage — that is the experiment)

- Cell-type identifiability on the target test split (kNN balanced accuracy / ARI / NMI).
- Global structure recovery (e.g. R² of the panel predicting top PCs of the full HVG space).
- **Developmental coverage** (specific to this skill): fraction of the Step-3 developmental set retained,
  and the panel's rank-coverage of the trajectory-associated genes.
- Compare against: target-stage-only panel of the same size (shows what the developmental component
  adds/costs), and any commercial fixed panel if relevant.

> Expect a small identifiability cost for the developmental component; report it honestly. The
> justification is that those genes are unrecoverable from the target stage.

## 6) Report

`report_analysis.md` must contain:
- the **lineage map** (1.3) and the **grouping** (3.1);
- trajectory construction details: root cell type, `isfinite` handling, method(s) used;
- the four rankings with the metric definitions, and how consensus was taken;
- the confounding check numbers (3.4);
- budget split, the **class A / class B anchor split** (3.3b), and the anchor list with each
  gene's class + supporting ranks;
- benchmark table incl. the target-stage-only comparison;
- a gene × {source, ranking evidence, lineage/category} table for the developmental component.

**Do not claim that a specific gene was "discovered as the top hit"** unless it literally was; report the
rank it achieved under each metric. The scientific claim of this workflow is about the *design rule*
(early reference + trajectory ranking recovers regulators that the target stage cannot), not about any
individual gene.
