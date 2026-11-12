# Single-cell label projection

Write `solution.py` defining `annotate(reference, query) -> labels`.

**Input.** Two `anndata.AnnData` from the same atlas, cells x the same 2000 highly variable genes:
- `X`: log-normalised expression (`log1p` of counts scaled to 10,000 per cell), sparse;
- `layers["counts"]`: raw counts, sparse;
- `obs["batch"]`: donor / lab / technology of each cell;
- `reference.obs["label"]`: the cell type of every reference cell. The query has no labels.

Query cells come from batches that are absent from the reference (other donors or technologies),
so the transfer must survive a batch effect. Every query cell's true type occurs in the reference.

**Output.** A sequence of `query.n_obs` labels (strings from the reference's label set), in query
order.

**Score.** Per dataset the mean of accuracy and macro F1 (each true cell type weighs equally, so
rare types matter); the score is the mean over several real atlases (different tissues and batch
structures). Higher is better. A program that errors, exceeds the time limit (300 s per dataset,
8 CPU threads, no GPU) or returns the wrong number of labels scores 0.

**Rules.** Available: numpy, scipy, pandas, scikit-learn, scanpy, anndata, igraph, leidenalg,
pynndescent, numba, jax (CPU). No network, no other packages, nothing read from disk except the
inputs given. The method must be general: it will also be scored on atlases it has never seen.
