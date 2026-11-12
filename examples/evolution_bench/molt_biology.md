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
  human immune;
- 10x Genomics PBMC 10k v3 (public sample data).

`tasks/_bio/prep_bio.py` turns them into fixed task inputs (seeded subsampling, 2000 highly
variable genes, seeded count split); the downloads and the prepared files live on Sherlock under
`$SCRATCH/evolve/biodata/`.

## Candidate tasks

| task | a program writes | score | public datasets | held-out datasets |
|---|---|---|---|---|
| `bio_integration` | `integrate(adata) -> embedding` | scIB total (scib-metrics, 0.6 bio + 0.4 batch, no min-max scaling) | pancreas, lung | immune |
| `bio_denoise` | `denoise(adata) -> matrix` | molecular cross-validation (90/10 count split), MSE + Poisson, 0 = no denoising, 1 = perfect | pancreas (UMI batches), lung | PBMC 10k, immune (UMI batches) |
| `bio_labelproj` | `annotate(reference, query) -> labels` | mean of accuracy and macro F1 on whole held-back batches | pancreas, lung | immune |

OpenProblems' perturbation-prediction task is left out: its data is on Kaggle behind an account.

Shared design, from what went wrong in the gene-panel study (fitness was a proxy that disagreed
with the benchmark):

- The fitness a method sees is the benchmark metric itself, on the public datasets. Held-out
  datasets (other tissues, donors, protocols) are read only for the final scores.
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
start program 3 times on the public datasets and once on the held-out ones, and every reference
once on each. Then one short run each of Molt and SimpleTES per task (seed 99, 100 evaluations,
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

A task that fails is reported with the reason. Whether to repair it (once, documented) or drop it
is decided with the user before the main runs.

## Step 2: main comparison (protocol as in molt_comparison.md)

- Methods: molt (frozen at b011425b), simpletes, openevolve, shinka, aide; seeds 0-4 per task.
- Model, reasoning setting, 400 evaluations, $4 LLM cap, 2000 LLM calls: as in the AHC comparison.
- Eval servers on `normal` with `CPU_SKU:7543`; seeds (0,1), (2,3), (4) of a task share a server.

## Analysis (fixed in advance)

1. Final score of a run: its top-3 distinct programs by recorded score are re-read 3 times each,
   interleaved, on the public datasets; the best mean is the pick (a failed read disqualifies a
   program). The pick is read 3 times on the held-out datasets; that mean is the final score. A
   run without a valid pick scores as its start program.
2. Per task: mean +/- sd per method; Welch t-test of Molt vs each method, Holm over four.
3. Primary endpoint, pooled over the biology tasks used: final scores normalised per task
   ((score - start) / (best score any run of the task reached - start)), paired by (task, seed);
   Wilcoxon signed-rank of Molt vs each method, Holm over four.
4. Against published methods: per task, how many runs of each method beat the best reference on
   the held-out datasets.
5. Cost per method: LLM dollars and evaluations.
