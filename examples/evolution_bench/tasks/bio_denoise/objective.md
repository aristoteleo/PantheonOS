# Single-cell denoising

Write `solution.py` defining `denoise(adata) -> matrix`.

**Input.** One `anndata.AnnData` per call: raw UMI/read counts, cells x 2000 genes (sparse
`adata.X`), from a real single-cell RNA-seq dataset. Single-cell counts are a sparse, noisy sample
of each cell's true expression.

**Output.** A non-negative float array of the same shape, in the same order: your estimate of each
cell's true expression of each gene (any overall scale; it is rescaled before scoring).

**Score.** Molecular cross-validation (Batson et al. 2019), as in the OpenProblems denoising task:
before you see the data, every count was split at random into the 90 % you get and 10 % held back.
Your output is rescaled to the held-back total and compared with the held-back counts by

- mse: mean squared error between log1p(output) and log1p(held-back counts),
- poisson: mean Poisson negative log-likelihood of the held-back counts.

Each is normalised so that returning the input unchanged scores 0 and predicting the held-back
counts exactly scores 1; a dataset scores their mean (floored at 0). The score is the mean over
several real datasets (human and mouse, different tissues and UMI protocols, about 4,000 cells
each). Higher is better. A program that errors,
exceeds the time limit (300 s per dataset, 8 CPU threads, no GPU) or returns a wrong shape or
negative values scores 0.

**Rules.** Available: numpy, scipy, pandas, scikit-learn, scanpy, anndata, igraph, leidenalg,
pynndescent, numba, jax (CPU). No network, no other packages, nothing read from disk except the
input given. The method must be general: the final score comes from datasets it has never seen -- other
tissues, other species (so other gene names) and other protocols, including single-nucleus data.
