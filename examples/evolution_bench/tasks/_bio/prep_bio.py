"""Build the fixed inputs of the single-cell tasks from the public downloads (run once, in a job).

    python prep_bio.py RAW_DIR OUT_DIR

RAW_DIR holds (see molt_biology.md for sources and the train / valid / test split):
  scIB atlases, figshare 12420968:  pancreas.h5ad lung.h5ad immune_human.h5ad
  CELLxGENE Discover:               hypomap.h5ad tms_muscle.h5ad kidney_dkd.h5ad heart.h5ad
                                    zebrafish_5dpf.h5ad gtex.h5ad
Everything random here uses fixed seeds, so the files are the same on every rebuild.

Per task and dataset: `<ds>.input.h5ad`, the only thing a program sees, and what the evaluator
scores against (`<ds>.eval.npz` labels for integration, `<ds>.test.npz` held-back counts for
denoising).
"""
import json
import sys
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp

RAW, OUT = Path(sys.argv[1]), Path(sys.argv[2])
N_HVG = 2000
N_INTEGRATION, N_DENOISE, N_DRAW = 8000, 4000, 40000

# scIB's own column names (batch, label)
SCIB = {"pancreas": ("tech", "celltype"), "lung": ("batch", "cell_type"),
        "immune_human": ("batch", "final_annotation")}
# CELLxGENE files (counts in .raw): batch = donor_id, label = cell_type (the schema's columns),
# except where the file itself rules that out (checked 2026-11-12, sherlock/bio_fetch.sbatch):
#   hypomap         92 % of cells have donor_id "pooled" -> the atlas's own Batch_ID (29 batches of 18
#                   studies); cell_type is 57 % "neuron" -> the atlas's 25-cluster level, C25_named
#   heart           donors were profiled as nuclei, cells and CD45-sorted cells -> donor x cell_source
#   zebrafish_5dpf  cell_type is "unknown" for 52 % of cells (anatomy terms have no cell-ontology
#                   match) -> the authors' annotation, zebrafish_anatomy_ontology_class
CXG = {"hypomap": (["Batch_ID"], "C25_named"), "tms_muscle": (["donor_id"], "cell_type"),
       "kidney_dkd": (["donor_id"], "cell_type"), "heart": (["donor_id", "cell_source"], "cell_type"),
       "zebrafish_5dpf": (["donor_id"], "zebrafish_anatomy_ontology_class"), "gtex": (["donor_id"], "cell_type")}
# Molecular cross-validation needs UMI counts: on deep full-length read counts (Smart-seq2, SMARTer,
# Fluidigm C1) the input alone already predicts the held-back 10 % and every smoother scores far
# below "no denoising" (pilot 2026-11-12: ALRA -3.7 on all-protocol pancreas). The denoising task
# keeps UMI batches only, as the OpenProblems denoising datasets are.
NON_UMI = {"pancreas": {"fluidigmc1", "smarter", "smartseq2"}, "immune_human": {"Villani"}}
UNLABELLED = {"unknown", "native cell", "nan", "na", "unassigned", ""}


def _counts(x):
    x = sp.csr_matrix(x, dtype=np.float32)
    x.data = np.rint(x.data)
    x.eliminate_zeros()
    return x


def load(name):
    """-> AnnData: X = counts (csr), obs batch / label (str categories), at most N_DRAW cells (seeded)."""
    if name in SCIB:
        a = sc.read_h5ad(RAW / f"{name}.h5ad")
        b, l = SCIB[name]
        x = a.layers["counts"] if "counts" in a.layers else a.X
        obs = a.obs[[b, l]].copy()
        idx = np.arange(a.n_obs)
        if a.n_obs > N_DRAW:
            idx = np.sort(np.random.default_rng(0).choice(a.n_obs, N_DRAW, replace=False))
        x, obs, var = x[idx], obs.iloc[idx], a.var_names
    else:
        a = ad.read_h5ad(RAW / f"{name}.h5ad", backed="r")
        idx = np.arange(a.n_obs)
        if a.n_obs > N_DRAW:
            idx = np.sort(np.random.default_rng(0).choice(a.n_obs, N_DRAW, replace=False))
        src = a.raw if a.raw is not None else a
        x = src.X[idx]
        bcols, lcol = CXG[name]
        o = a.obs.iloc[idx]
        batch = o[bcols[0]].astype(str)
        for c in bcols[1:]:
            batch = batch + "/" + o[c].astype(str)
        obs = pd.DataFrame({"batch": batch.values, "label": o[lcol].astype(str).values})
        col = next((c for c in ("feature_name", "gene_name") if c in src.var), None)
        var = src.var[col].astype(str) if col else src.var_names
    out = ad.AnnData(X=_counts(x), obs=obs.reset_index(drop=True))
    out.var_names = [str(v) for v in var]
    out.var_names_make_unique()
    out.obs.columns = ["batch", "label"]
    out.obs = out.obs.astype({"batch": str, "label": str}).astype("category")
    out.obs_names = [str(i) for i in range(out.n_obs)]
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
    """Top N_HVG genes by scanpy's cell_ranger flavour on log-normalised counts, batch-aware where
    batches exist (scIB's own HVG selection)."""
    x = ad.AnnData(X=lognorm(a.X), obs=a.obs.copy(), var=a.var[[]].copy())
    sc.pp.highly_variable_genes(x, n_top_genes=N_HVG, flavor="cell_ranger",
                                batch_key="batch" if batch and "batch" in x.obs else None)
    return a[:, x.var["highly_variable"].values].copy()


def integration(name, a):
    """Input: log-normalised HVG matrix + counts + batch. Labels only in the eval file. Unlabelled
    cells, cell types under 10 cells and batches under 50 cells are left out (same rule everywhere)."""
    a = a[~a.obs["label"].str.lower().isin(UNLABELLED).values].copy()
    a = subsample(a, N_INTEGRATION)
    for col, least in (("label", 10), ("batch", 50), ("label", 10)):
        n = a.obs[col].astype(str).value_counts()
        a = a[a.obs[col].astype(str).isin(n.index[n >= least]).values].copy()
    a.obs = a.obs.astype(str).astype("category")
    sc.pp.filter_genes(a, min_cells=3)
    a = hvg(a)
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
            "labels": a.obs["label"].nunique(), "smallest_batch": int(a.obs["batch"].value_counts().min()),
            "batch_sizes": a.obs["batch"].value_counts().to_dict(), "label_sizes": a.obs["label"].value_counts().to_dict(),
            "gene_names": list(a.var_names[:5])}


def denoise(name, a):
    """Molecular cross-validation (Batson et al. 2019): each count is split binomially, 90 % to the
    input, 10 % held back; a program denoises the 90 % and is scored on the 10 %."""
    a = subsample(a, N_DENOISE)
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
    return {"cells": int(keep.sum()), "genes": a.n_vars,
            "median_input_counts_per_cell": float(np.median(np.asarray(tr.sum(1)).ravel())),
            "batches": sorted(map(str, a.obs["batch"].unique())) if "batch" in a.obs else None}


if __name__ == "__main__":
    info = {}
    for name in list(SCIB) + list(CXG):
        a = load(name)
        info[f"integration/{name}"] = integration(name, a)
        info[f"denoise/{name}"] = denoise(name, a[~a.obs["batch"].isin(NON_UMI.get(name, set())).values].copy())
        print(name, json.dumps({k: v for k, v in info.items() if k.endswith("/" + name)}, default=str), flush=True)
    (OUT / "prep_info.json").write_text(json.dumps(info, indent=1, default=str))
    print(json.dumps(info, indent=1, default=str))
