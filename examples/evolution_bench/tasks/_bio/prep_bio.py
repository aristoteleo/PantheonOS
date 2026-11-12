"""Build the fixed inputs of the single-cell tasks from the public downloads (run once, in a job).

    python prep_bio.py RAW_DIR OUT_DIR

RAW_DIR holds the scIB datasets from figshare article 12420968 (pancreas.h5ad, lung.h5ad,
immune_human.h5ad) and 10x PBMC 10k v3 (pbmc10k_v3.h5); see molt_biology.md. Everything random
here uses fixed seeds, so the files are the same on every rebuild.

Per task and dataset two files: `<ds>.input.h5ad`, the only thing a program sees, and
`<ds>.eval.npz`, what the evaluator scores against (labels, held-out counts, ...).
"""
import json
import sys
from pathlib import Path

import anndata as ad
import numpy as np
import scanpy as sc
import scipy.sparse as sp

RAW, OUT = Path(sys.argv[1]), Path(sys.argv[2])
# scIB's own column names
SCIB = {"pancreas": ("tech", "celltype"), "lung": ("batch", "cell_type"),
        "immune_human": ("batch", "final_annotation")}
N_HVG = 2000


def load_scib(name):
    a = sc.read_h5ad(RAW / f"{name}.h5ad")
    b, l = SCIB[name]
    counts = a.layers["counts"] if "counts" in a.layers else a.X
    out = ad.AnnData(X=sp.csr_matrix(counts, dtype=np.float32), obs=a.obs[[b, l]].copy(),
                     var=a.var[[]].copy())
    out.obs.columns = ["batch", "label"]
    out.obs = out.obs.astype({"batch": str, "label": str}).astype("category")
    out.obs_names = [str(x) for x in range(out.n_obs)]
    return out


def subsample(a, n, seed=0):
    if a.n_obs <= n:
        return a.copy()
    idx = np.sort(np.random.default_rng(seed).choice(a.n_obs, n, replace=False))
    return a[idx].copy()


def lognorm(counts):
    x = ad.AnnData(X=counts.copy())
    sc.pp.normalize_total(x, target_sum=1e4)
    sc.pp.log1p(x)
    return x.X


def hvg(a, batch=True):
    """Top N_HVG genes by seurat_v3 on counts (batch-aware where batches exist)."""
    x = a.copy()
    sc.pp.highly_variable_genes(x, n_top_genes=N_HVG, flavor="seurat_v3",
                                batch_key="batch" if batch and "batch" in x.obs else None)
    return a[:, x.var["highly_variable"].values].copy()


def integration(name):
    """Input: log-normalised HVG matrix + counts + batch. Labels only in the eval file."""
    a = hvg(subsample(load_scib(name), 12000))
    sc.pp.filter_cells(a, min_counts=1)
    inp = ad.AnnData(X=sp.csr_matrix(lognorm(a.X)), obs=a.obs[["batch"]].copy(), var=a.var[[]].copy())
    inp.layers["counts"] = a.X.copy()
    x = inp.copy()
    sc.pp.scale(x, max_value=10)
    sc.tl.pca(x, n_comps=50, random_state=0)
    d = OUT / "integration"
    d.mkdir(parents=True, exist_ok=True)
    inp.write_h5ad(d / f"{name}.input.h5ad", compression="gzip")
    np.savez_compressed(d / f"{name}.eval.npz", label=a.obs["label"].astype(str).values,
                        batch=a.obs["batch"].astype(str).values, X_pca=x.obsm["X_pca"].astype(np.float32))
    return {"cells": a.n_obs, "genes": a.n_vars, "batches": a.obs["batch"].nunique(),
            "labels": a.obs["label"].nunique()}


def denoise(name, a):
    """Molecular cross-validation (Batson et al. 2019): each count is split binomially, 90 % to the
    input, 10 % held back; a program denoises the 90 % and is scored on the 10 %."""
    a = subsample(a, 4000)
    a = a[:, np.asarray((a.X > 0).sum(0)).ravel() >= 10].copy()
    a = hvg(a, batch=False)
    rng = np.random.default_rng(1)
    X = a.X.tocsr().copy()
    X.data = np.rint(X.data)
    X = X.astype(np.int64)
    tr = X.copy()
    tr.data = rng.binomial(X.data, 0.9)
    te = X - tr
    keep = (np.asarray(tr.sum(1)).ravel() > 0) & (np.asarray(te.sum(1)).ravel() > 0)
    tr, te = tr[keep].astype(np.float32), te[keep].astype(np.float32)
    d = OUT / "denoise"
    d.mkdir(parents=True, exist_ok=True)
    ad.AnnData(X=sp.csr_matrix(tr), var=a.var[[]].copy()).write_h5ad(d / f"{name}.input.h5ad",
                                                                     compression="gzip")
    sp.save_npz(d / f"{name}.test.npz", sp.csr_matrix(te))
    return {"cells": int(keep.sum()), "genes": a.n_vars, "train_counts": float(tr.sum()),
            "test_counts": float(te.sum())}


def labelproj(name):
    """Reference = labelled batches; query = a fixed ~30 % of batches (seeded draw), labels hidden.
    Query cells whose type never occurs in the reference are dropped (nothing could name them)."""
    a = hvg(load_scib(name))
    batches = sorted(a.obs["batch"].unique())
    rng = np.random.default_rng(2)
    q = sorted(rng.choice(batches, max(1, int(np.ceil(0.3 * len(batches)))), replace=False).tolist())
    isq = a.obs["batch"].isin(q).values
    ref, qry = subsample(a[~isq], 15000, 3), subsample(a[isq], 5000, 4)
    qry = qry[qry.obs["label"].isin(set(ref.obs["label"])).values].copy()
    d = OUT / "labelproj"
    d.mkdir(parents=True, exist_ok=True)
    for part, x, labelled in (("reference", ref, True), ("query", qry, False)):
        o = ad.AnnData(X=sp.csr_matrix(lognorm(x.X)),
                       obs=x.obs[["batch", "label"] if labelled else ["batch"]].copy(), var=x.var[[]].copy())
        o.layers["counts"] = x.X.copy()
        o.write_h5ad(d / f"{name}.{part}.input.h5ad", compression="gzip")
    np.savez_compressed(d / f"{name}.eval.npz", label=qry.obs["label"].astype(str).values)
    return {"query_batches": q, "reference_cells": ref.n_obs, "query_cells": qry.n_obs,
            "labels": ref.obs["label"].nunique()}


if __name__ == "__main__":
    info = {}
    for name in SCIB:
        info[f"integration/{name}"] = integration(name)
        info[f"labelproj/{name}"] = labelproj(name)
        info[f"denoise/{name}"] = denoise(name, load_scib(name))
    p = sc.read_10x_h5(RAW / "pbmc10k_v3.h5")
    p.var_names_make_unique()
    p.X = sp.csr_matrix(p.X, dtype=np.float32)
    info["denoise/pbmc10k"] = denoise("pbmc10k", p)
    (OUT / "prep_info.json").write_text(json.dumps(info, indent=1, default=str))
    print(json.dumps(info, indent=1, default=str))
