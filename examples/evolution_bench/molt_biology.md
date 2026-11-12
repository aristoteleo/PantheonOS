# Molt on single-cell biology tasks

Claim 2 of the Molt paper plan: Molt's lead over the known program-search methods holds on
biology tasks, not only on AtCoder heuristics. Written 2026-11-11, before any pilot result; the
selection rule and the analysis below are fixed in advance.

## Data source

The plan was the OpenProblems tasks (shared datasets, metrics and published method scores). Their
data bucket (`s3://openproblems-data`) has answered every request with 403 AllAccessDisabled since
2026-11-05 (openproblems-bio/task_predict_modality#85, no reply yet). The tasks are therefore
rebuilt from the public sources the OpenProblems tasks themselves are based on:

- the scIB atlases (Luecken et al. 2022, figshare article 12420968): human pancreas, lung atlas,
  human immune.
- six CELLxGENE Discover datasets (public, CC BY 4.0; `sherlock/bio_fetch.sbatch`), added on
  2026-11-12 for the train / valid / test split below.

`tasks/_bio/prep_bio.py` turns them into fixed task inputs (seeded subsampling, 2000 highly
variable genes, seeded count split); the downloads and the prepared files live on Sherlock under
`$SCRATCH/evolve/biodata/`.

## Tasks

| task | a program writes | score |
|---|---|---|
| `bio_integration` | `integrate(adata) -> embedding` | scIB total (scib-metrics, 0.6 bio + 0.4 batch, no min-max scaling) |
| `bio_denoise` | `denoise(adata) -> matrix` | molecular cross-validation (90/10 count split), MSE + Poisson, 0 = no denoising, 1 = perfect |

Label projection (`annotate(reference, query) -> labels`) was a third candidate and failed the
selection rule below (rules 3 and 4); dropped with the user on 2026-11-12. OpenProblems'
perturbation-prediction task is left out: its data is on Kaggle behind an account.

## Train / valid / test (2026-11-12, asked for by the user before the main runs)

The first design searched on two datasets and scored on one or two more, with a run's final program
picked on the search data. Now three disjoint sets of studies, shared by both tasks:

| split | read by | dataset | species, tissue | batches | protocols |
|---|---|---|---|---|---|
| train | every evaluation of a search | pancreas (scIB) | human pancreas | technologies / studies | CEL-seq, CEL-seq2, inDrop, SMART-seq2, SMARTer, Fluidigm C1 |
| | | lung (scIB) | human lung | donors of 3 labs | 10x, Drop-seq |
| | | hypomap (CELLxGENE, 10.1038/s42255-022-00657-y) | mouse hypothalamus | the atlas's 29 batches of 18 studies | 10x v1-v3, Drop-seq; cells and nuclei |
| valid | once per run, to pick its final program | immune_human (scIB) | human bone marrow + blood | studies / donors | 10x, Smart-seq2 |
| | | tms_muscle (Tabula Muris Senis, 10.1038/s41586-020-2496-1) | mouse limb muscle | 16 mice, 1 to 30 months | 10x v2 |
| test | only for that pick: the final score | kidney_dkd (10.1038/s41467-022-32972-z) | human kidney cortex, healthy + diabetic | 11 donors | single-nucleus, 10x 3' v3 and 5' v1 |
| | | heart (10.1038/s41586-020-2797-4) | human heart, 6 regions | donor x preparation (nuclei, cells, CD45-sorted cells) | 10x v2 / v3 |
| | | zebrafish_5dpf (Zebrahub, 10.1101/2023.03.06.531398) | zebrafish larva, whole body | 4 fish | 10x v3 |
| | | gtex (10.1126/science.abl4290) | 8 human tissues | donors | single-nucleus, 10x v2 |

- No study appears in two splits. (10x's PBMC 10k v3 sample, a denoising dataset of the first design, is
  one of the batches of scIB's immune atlas, now in valid: left out.) Test holds what train and valid do not: kidney, heart, a
  non-mammal, an 8-tissue set, and single-nucleus-only data. Train and valid both mix human and
  mouse, so a method tied to one species' gene names fails before test.
- CELLxGENE files share one schema: batch = `donor_id`, label = `cell_type`, counts from `.raw`.
  Three files rule that out and use the authors' own columns instead (checked on the downloads,
  before any read): hypomap (92 % of cells have donor "pooled"; `cell_type` is 57 % "neuron") ->
  `Batch_ID` and the atlas's 25-cluster level `C25_named`; heart (donors profiled as nuclei, cells
  and CD45-sorted cells) -> donor x `cell_source`; zebrafish (`cell_type` "unknown" for 52 % of
  cells) -> `zebrafish_anatomy_ontology_class`.
  Integration: 8,000 cells per dataset (seeded draw), 2000 batch-aware HVGs; unlabelled cells, cell
  types under 10 cells and batches under 50 cells left out. Denoising: 4,000 cells, 2000 HVGs, UMI
  batches only.
- `BIO_SPLIT` selects the split an eval server reads (`train` by default); the valid and test
  inputs are only read by the final measurement.

Shared design, from what went wrong in the gene-panel study (fitness was a proxy that disagreed
with the benchmark):

- The fitness a method sees is the benchmark metric itself, on the train datasets. Valid and
  test datasets are read only by the final measurement.
- A program sees only its inputs (copied to a temporary directory), never labels the benchmark
  hides, and runs in a fresh process: 8 threads, 300 s per dataset, no GPU, no network.
  Libraries: numpy, scipy, pandas, scikit-learn, scanpy, anndata, igraph, leidenalg, pynndescent,
  numba, jax. Packages implementing published methods (harmonypy, scanorama, magic-impute) are
  only in a separate reference environment, so a program has to implement any method it uses.
- The start program is the simplest standard baseline: PCA without correction; no denoising;
  15-NN vote in PCA space.
- Known methods are scored by the same evaluator on the same files (`tasks/_bio/refs/`):
  ComBat, Harmony, Scanorama; MAGIC, ALRA (re-implemented from the paper), kNN smoothing;
  logistic regression, MLP, Harmony + kNN.

Environments (Sherlock rules forbid environments on `$SCRATCH`): `$GROUP_HOME/wzxu/venv-bio`
(eval servers) and `$GROUP_HOME/wzxu/venv-bio-ref` (same + harmonypy, scanorama, magic-impute),
both run inside `py312.sif`; versions in the `.freeze.txt` files beside them.

## Step 1: pilot and task selection (rule fixed in advance)

`tasks/_bio/pilot_bio.py` on an EPYC 7543 node (`CPU_SKU:7543`, the eval-server CPU) reads the
start program 3 times on the search datasets and once on the others, and every reference once on
each split. (Written for the first two-way split, "public" / "held-out"; re-applied to the
three-way split with public = train and held-out = test.) Then one short run each of Molt and SimpleTES per task (seed 99, 100 evaluations,
otherwise the main protocol); these runs are not part of any comparison.

Headroom = best reference's public score - start's public score. A task is used if all hold:

1. Time: a public read of the start program takes <= 120 s.
2. Noise: the 3 public reads of the start spread (max - min) by <= 0.1 x headroom.
3. Headroom: >= 0.05.
4. Transfer: Spearman correlation between public and held-out scores over the start and the
   references >= 0.4 (public improvements carry over to unseen datasets).
5. Movement: the better of the two short runs gains >= 0.25 x headroom over the start on the
   public datasets. Only the better run is used, never the difference between the two.

Construction fixes before the counted pilot reads (2026-11-12; a first pilot run found them, its
reads are kept in `biodata/pilot_try1.json` and not counted):

- HVG selection: scanpy's `cell_ranger` flavour on log-normalised counts, batch-aware (scIB's own
  choice; `seurat_v3` needs an extra package).
- scib-metrics 0.6.2 fails under umap-learn 0.6.0 (numba typing error in `compute_membership_strengths`);
  umap-learn pinned to 0.5.7. annoy (Scanorama) rebuilt on an EPYC 7543: built on a Zen4 node with
  `-march=native` it died with SIGILL. The ComBat reference copied combat's read-only output.
- Denoising uses UMI batches only. On deep full-length read counts (pancreas Smart-seq2 / SMARTer /
  Fluidigm C1, immune Villani) the input already predicts the held-back counts and every smoother
  scored far below "no denoising" (ALRA -3.7 on all-protocol pancreas): molecular cross-validation
  is a UMI-data method, and the OpenProblems denoising datasets are UMI data.

### Pilot result on the first, two-way split (2026-11-12, EPYC 7543; `biodata/pilot_2way.json`, runs in `results/biopilot/`)

| task | start public (3 reads) | best reference public | headroom | read spread | rho public vs held-out | public read | best short run | verdict |
|---|---|---|---|---|---|---|---|---|
| `bio_integration` | 0.5572 / 0.5573 / 0.5572 | ComBat 0.6656 | 0.108 | 0.0001 | 0.8 | 43-116 s | SimpleTES 0.687 (20 evals) | used |
| `bio_denoise` | 0 / 0 / 0 | kNN smoothing 0.2432 | 0.243 | 0 | ~0.95 (ALRA, start tie at 0 on held-out) | 4 s | SimpleTES 0.334, Molt 0.327 (50 evals) | used |
| `bio_labelproj` | 0.8273 x 3 | MLP 0.8535 | 0.026 (rule 3 fails) | 0 | 0.2 (rule 4 fails) | 12 s | not run | fails 3, 4 |

Held-out references: integration ComBat 0.634, Harmony 0.673, Scanorama 0.628, start 0.547; denoising
MAGIC 0.262, kNN smoothing 0.277, ALRA 0, start 0; label projection start 0.881 (above every
reference: logistic regression 0.875, MLP 0.864, Harmony + kNN 0.860).

The start programs carry `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` around the whole file, as the
AHC whole-file seeds do: ShinkaEvolve refuses every edit outside such a block (smoke test 2026-11-12:
9 proposals, 0 children without the markers; 4 children, best 0.269, with them). The short Molt /
SimpleTES runs started from the unmarked file (comments only, same program).

### Pilot result on the train / valid / test split (2026-11-12, EPYC 7543; `biodata/pilot.json`, inputs in `biodata/prep/prep_info.json`)

The rule re-applied with public = train and held-out = test. `pilot-molt` / `pilot-simpletes` are
the best programs of the two short runs above (they searched on the first split's pancreas + lung
at 12,000 cells), read once per split like a reference.

| `bio_integration` | train | valid | test | test per dataset (kidney / heart / zebrafish / gtex) |
|---|---|---|---|---|
| start (PCA) | 0.5347 / 0.5349 / 0.5349 | 0.629 | 0.595 | 0.569 / 0.548 / 0.674 / 0.588 |
| Scanorama | 0.568 | 0.677 | 0.606 | 0.643 / 0.506 / 0.695 / 0.580 |
| ComBat | 0.645 | 0.724 | 0.676 | 0.671 / 0.650 / 0.741 / 0.643 |
| Harmony | 0.665 | 0.729 | 0.665 | 0.717 / 0.618 / 0.718 / 0.606 |
| pilot-molt | 0.637 | 0.728 | 0.680 | 0.693 / 0.648 / 0.739 / 0.642 |
| pilot-simpletes | 0.659 | 0.704 | 0.681 | 0.670 / 0.671 / 0.739 / 0.643 |

| `bio_denoise` | train | valid | test | test per dataset (kidney / heart / zebrafish / gtex) |
|---|---|---|---|---|
| start (no denoising) | 0 / 0 / 0 | 0 | 0 | 0 / 0 / 0 / 0 |
| ALRA | 0.085 | 0 | 0 | 0 / 0 / 0 / 0 |
| MAGIC | 0.207 | 0.182 | 0.121 | 0.110 / 0.171 / 0.016 / 0.184 |
| kNN smoothing | 0.239 | 0.205 | 0.151 | 0.160 / 0.209 / 0.020 / 0.216 |
| pilot-molt | 0.321 | 0.305 | 0.287 | 0.286 / 0.294 / 0.265 / 0.305 |
| pilot-simpletes | 0.324 | 0.307 | 0.273 | 0.275 / 0.296 / 0.213 / 0.308 |

| rule | `bio_integration` | `bio_denoise` |
|---|---|---|
| 1 time of a train read of the start (<= 120 s) | 48 s (first read 124 s: compile warm-up, which a server does at start) | 5 s |
| 2 spread of 3 reads (<= 0.1 x headroom) | 0.0002 | 0 |
| 3 headroom (>= 0.05) | 0.130 (Harmony) | 0.239 (kNN smoothing) |
| 4 Spearman train vs test, start + references (>= 0.4) | 0.8 (0.6 with the two pilot programs) | 0.95 (0.93 with them) |
| 5 movement of the better short run on train (>= 0.25 x headroom) | +0.124 | +0.324 |

Both tasks pass. Train improvements carry to valid and to test, including to the zebrafish and
single-nucleus datasets no search saw; the two programs found by the short runs are level with the
best reference on the integration test set and well above it on denoising (0.27-0.29 vs 0.15).
ALRA (re-implemented from the paper) only scores on lung: its zero-thresholding costs more Poisson
likelihood than it gains; kept as a reference, the two that count are kNN smoothing and MAGIC (the
top methods of the OpenProblems denoising task). A train read of an evolved program takes
100-220 s (integration) and 10-130 s (denoising).

A task that fails is reported with the reason. Whether to repair it (once, documented) or drop it
is decided with the user before the main runs.

## Step 2: main comparison (protocol as in molt_comparison.md)

- Methods: molt (frozen at b011425b), simpletes, openevolve, shinka, aide; seeds 0-4 per task.
- Model, reasoning setting, 400 evaluations, $4 LLM cap, 2000 LLM calls: as in the AHC comparison.
- Every method starts from the task's own start program (`tasks/<task>/solution.py`).
- Eval servers (`sherlock/eval_server_bio.sbatch`, train datasets only) on `normal` with
  `CPU_SKU:7543`, 8 CPUs each, one evaluation at a time. Every integration run has its own server
  (an evaluation takes 100-220 s, so 400 of them need 11-25 h); on denoising (10-130 s) each seed
  has three (molt + simpletes, openevolve + shinka, aide). Sharing only changes how long a run
  waits, never a score.
- Spec: `sherlock/make_bio_main.py` -> `specs/bio_main.json`, results in `results/biomain/`.

## Analysis (fixed in advance)

Scripts: `tasks/_bio/measure_bio.py` (step 1), `tasks/_bio/stats_bio.py` (steps 2-5).

1. Final score of a run: its top-5 distinct programs by recorded (train) score are each read once
   on the valid datasets; the best valid score is the pick (a program that fails on valid is out;
   ties go to the higher train score). The pick is read 3 times on the test datasets; that mean is
   the final score. A run with no program passing valid scores as its start program. Nothing read
   on test feeds back into any choice.
2. Per task: mean +/- sd per method; Welch t-test of Molt vs each method, Holm over four.
3. Primary endpoint, pooled over the two biology tasks (10 pairs per comparison): final scores normalised per task
   ((score - start) / (best score any run of the task reached - start)), paired by (task, seed);
   Wilcoxon signed-rank of Molt vs each method, Holm over four.
4. Against published methods: per task, how many runs of each method beat the best reference on
   the test datasets; and the per-dataset test scores of every method's picks next to the
   references', so a lead carried by one dataset shows.
5. Cost per method: LLM dollars and evaluations.
