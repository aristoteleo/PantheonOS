"""Single-cell batch integration, scored with the scIB metrics (Luecken et al. 2022, Nat Methods).

`solution.py` defines `integrate(adata) -> embedding` (cells x d, 2 <= d <= 100). The AnnData holds
log-normalised counts of 2000 highly variable genes in `X`, raw counts in `layers["counts"]` and
the batch in `obs["batch"]`; cell types are never given. The embedding is scored with
scib-metrics' Benchmarker (default bio-conservation and batch-correction metrics, no min-max
scaling, so a score does not depend on what else is benchmarked): total = 0.6 bio + 0.4 batch.

combined_score = mean total over the split's datasets (train / valid / test are disjoint studies,
see _bio/biocommon.py; a search only ever sees train).
Any dataset failing (error, time limit, bad shape) scores the program 0.
"""
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(os.environ.get("TASK_DIR") or globals().get("__file__", ".")).resolve()
sys.path.insert(0, str(HERE.parent / "_bio"))
from biocommon import DATA, SPLIT, SPLITS, fail, fidelity_of, run_program  # noqa: E402

DATASETS = SPLITS["integration"]   # train / valid / test: disjoint studies, see _bio/biocommon.py
N_JOBS = int(os.environ.get("BIO_THREADS", "8"))


def scib_total(emb: np.ndarray, ev) -> dict:
    import anndata as ad
    from scib_metrics.benchmark import BatchCorrection, Benchmarker, BioConservation

    a = ad.AnnData(X=np.zeros((emb.shape[0], 1), dtype=np.float32))
    a.obs["batch"] = ev["batch"]
    a.obs["label"] = ev["label"]
    a.obs = a.obs.astype("category")
    a.obsm["X_pca"] = ev["X_pca"]
    a.obsm["X_emb"] = emb
    bm = Benchmarker(a, batch_key="batch", label_key="label", embedding_obsm_keys=["X_emb"],
                     pre_integrated_embedding_obsm_key="X_pca", bio_conservation_metrics=BioConservation(),
                     batch_correction_metrics=BatchCorrection(), n_jobs=N_JOBS, progress_bar=False)
    bm.benchmark()
    df = bm.get_results(min_max_scale=False)
    row = df.loc["X_emb"]
    return {k: float(v) for k, v in row.items() if isinstance(v, (int, float, np.floating))}


def evaluate(workspace_path: str, fidelity: str = "full") -> dict:
    t0 = time.time()
    names = DATASETS[SPLIT]
    if fidelity_of(workspace_path, fidelity) == "low":
        names = names[:1]
    out = {}
    for name in names:
        inp = DATA / "integration" / f"{name}.input.h5ad"
        ev = np.load(DATA / "integration" / f"{name}.eval.npz", allow_pickle=True)
        path, err, secs = run_program(workspace_path, "integrate", [inp], f"{name}.npy")
        if path is None:
            return fail(f"{name}: {err}", t0)
        emb = np.load(path)
        n = len(ev["label"])
        if emb.ndim != 2 or emb.shape[0] != n or not 2 <= emb.shape[1] <= 100:
            return fail(f"{name}: embedding shape {emb.shape}, need ({n}, 2..100)", t0)
        if not np.all(np.isfinite(emb)) or float(emb.std(0).max()) == 0.0:
            return fail(f"{name}: embedding has non-finite values or is constant", t0)
        try:
            m = scib_total(emb, ev)
        except Exception as e:  # noqa: BLE001
            return fail(f"{name}: scoring failed: {type(e).__name__}: {e}", t0)
        out[name] = {"total": m.get("Total"), "bio": m.get("Bio conservation"),
                     "batch": m.get("Batch correction"), "solve_seconds": round(secs, 1), "metrics": m}
    combined = float(np.mean([v["total"] for v in out.values()]))
    return {"combined_score": combined, "validity": 1.0, "datasets": out,
            **{f"{k}_total": v["total"] for k, v in out.items()},
            "eval_time": time.time() - t0, "fitness_weights": {"combined_score": 1.0}}


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate(sys.argv[1] if len(sys.argv) > 1 else str(HERE),
                              sys.argv[2] if len(sys.argv) > 2 else "full"), indent=1, default=str))
