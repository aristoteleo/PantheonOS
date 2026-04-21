"""Gene Panel Selection — helper functions for the omics/gene_panel_selection skill.

Ship the three panel-selection algorithms (SpaPROS, Random Forest, scGeneFit)
plus the SpaPROS runtime estimator in one plain-Python file. The agent is
expected to ``%run`` this file (or ``sys.path.insert`` + import) from inside
a notebook cell when executing Step 2 of the workflow described in SKILL.md.

Design notes:
- No registered toolset, no ``GenePanelConfig`` class, no ``settings.json``
  integration. Hyperparameters are module-level constants at the top of this
  file; override per call with kwargs when needed.
- Each ``select_*`` function: reads an .h5ad, runs the upstream library,
  writes CSVs to ``{workdir}/gene_panels/{method}/``, returns a dict.
- With ``return_scores=True`` the full score ranking lands on disk and is
  returned — call once, slice top-K in pandas for Step 3's ARI-vs-size sweep.
"""

from __future__ import annotations

import os
from typing import Any, Optional


# ─── Hyperparameter defaults ────────────────────────────────────────────────
# Caller can override any of these with kwargs. These live here (not in
# settings.json) so the skill is self-contained and the agent can read the
# intent + values in one place.

SCGENEFIT_MAX_CONSTRAINTS = 1000          # LP constraint cap
SPAPROS_N_HVG = 3000                      # HVG pre-filter before SpaPROS
RF_N_ESTIMATORS = 300                     # Random Forest trees
ARI_DROP_THRESHOLD = 0.05                 # Step 4 curation stability gate
DOWNSAMPLE_MAX_CELLS = 500_000            # Step 1 downsampling trigger
GENE_COUNT_THRESHOLD = 30_000             # Step 1 gene filtering trigger
SPLIT_CELL_LIMIT = 50_000                 # Step 1 target cells per split
N_TRAINING_SPLITS = 1                     # Step 1 train splits
N_TEST_SPLITS = 5                         # Step 1 min test splits
SPAPROS_RUNTIME_WARNING_MINUTES = 5.0     # SpaPROS "fast" → "slow" boundary
SPAPROS_RUNTIME_SKIP_MINUTES = 30.0       # SpaPROS "slow" → "very_slow" boundary

# SpaPROS runtime estimate heuristics (observed on typical scRNA-seq data)
_SPAPROS_SECS_PER_10K_CELLS_PER_3K_HVG = 180.0
_SPAPROS_SECS_PER_100_MARKERS = 60.0


def _require_path(adata_path: Optional[str]) -> str:
    if not adata_path:
        raise ValueError("adata_path is required.")
    if not os.path.isfile(adata_path):
        raise FileNotFoundError(f"adata file not found: {adata_path}")
    return adata_path


# ─── SpaPROS ────────────────────────────────────────────────────────────────


def select_spapros(
    adata_path: str,
    label_key: str,
    num_markers: int = 100,
    n_hvg: int = SPAPROS_N_HVG,
    return_scores: bool = True,
    workdir: str = ".",
) -> dict:
    """Select marker genes with SpaPROS probe-set selection.

    Writes ``spapros_full_table.csv``, ``spapros_top_{N}.csv``, and
    (when ``return_scores``) ``spapros_scores.csv`` to
    ``{workdir}/gene_panels/spapros/``.
    """
    try:
        import scanpy as sc
        import pandas as pd
        import numpy as np
        import spapros as sp
    except ImportError as e:
        return {"error": f"SpaPROS needs scanpy/pandas/numpy/spapros. `pip install spapros`. {e}"}

    _require_path(adata_path)
    out_dir = os.path.join(workdir, "gene_panels", "spapros")
    os.makedirs(out_dir, exist_ok=True)

    adata = sc.read_h5ad(adata_path)
    if label_key not in adata.obs.columns:
        return {"error": f"label_key '{label_key}' not found in adata.obs."}

    sc.pp.highly_variable_genes(adata, flavor="cell_ranger", n_top_genes=n_hvg)
    adata = adata[:, adata.var["highly_variable"]]

    selector = sp.se.ProbesetSelector(
        adata,
        n=num_markers,
        celltype_key=label_key,
        verbosity=1,
        save_dir=None,
    )
    selector.select_probeset()

    df = selector.probeset.copy()
    df.index.name = "gene"
    full_path = os.path.join(out_dir, "spapros_full_table.csv")
    df.to_csv(full_path)

    selected = df[df["selection"] == True].index.tolist()
    panel_path = os.path.join(out_dir, f"spapros_top_{num_markers}.csv")
    pd.DataFrame({"gene": selected}).to_csv(panel_path, index=False)

    result = {
        "used_dataset": adata_path,
        "top_n": num_markers,
        "saved_to": {"panel": panel_path, "full_table": full_path},
        "genes": selected,
    }
    if return_scores and "importance_score" in df.columns:
        scores = [
            {"gene": g, "score": float(row.get("importance_score", np.nan))}
            for g, row in df.iterrows()
        ]
        score_path = os.path.join(out_dir, "spapros_scores.csv")
        pd.DataFrame(scores).to_csv(score_path, index=False)
        result["saved_to"]["scores"] = score_path
        result["genes"] = scores
    return result


# ─── Random Forest ──────────────────────────────────────────────────────────


def select_random_forest(
    adata_path: str,
    label_key: str,
    n_top_genes: int = 1000,
    n_estimators: int = RF_N_ESTIMATORS,
    return_scores: bool = True,
    random_state: int = 42,
    workdir: str = ".",
) -> dict:
    """Rank genes by Random Forest feature importance (Gini).

    Trains an RF classifier predicting ``label_key`` from expression, then
    ranks genes by ``feature_importances_``. ``return_scores=True`` writes
    and returns the full ranking.
    """
    try:
        import scanpy as sc
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import RandomForestClassifier
    except ImportError as e:
        return {"error": f"RF needs scanpy/scikit-learn. `pip install scanpy scikit-learn`. {e}"}

    _require_path(adata_path)
    out_dir = os.path.join(workdir, "gene_panels", "random_forest")
    os.makedirs(out_dir, exist_ok=True)

    adata = sc.read_h5ad(adata_path)
    if label_key not in adata.obs.columns:
        return {"error": f"label_key '{label_key}' not found in adata.obs."}

    X = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
    y = adata.obs[label_key].astype("category").cat.codes.values

    clf = RandomForestClassifier(
        n_estimators=n_estimators, random_state=random_state, n_jobs=-1
    )
    clf.fit(X, y)

    ranked = sorted(
        (
            {"gene": g, "score": float(s)}
            for g, s in zip(adata.var_names, clf.feature_importances_)
        ),
        key=lambda d: d["score"],
        reverse=True,
    )

    panel_path = os.path.join(out_dir, f"rf_top_{n_top_genes}.csv")
    pd.DataFrame(ranked[:n_top_genes]).to_csv(panel_path, index=False)

    result = {
        "used_dataset": adata_path,
        "top_n": n_top_genes,
        "saved_to": {"panel": panel_path},
        "genes": [r["gene"] for r in ranked[:n_top_genes]],
    }
    if return_scores:
        score_path = os.path.join(out_dir, "rf_scores.csv")
        pd.DataFrame(ranked).to_csv(score_path, index=False)
        result["saved_to"]["scores"] = score_path
        result["genes"] = ranked
    return result


# ─── scGeneFit ──────────────────────────────────────────────────────────────


def select_scgenefit(
    adata_path: str,
    label_key: str,
    n_top_genes: int = 200,
    method: str = "centers",
    epsilon_param: float = 1.0,
    sampling_rate: float = 1.0,
    n_neighbors: int = 3,
    max_constraints: int = SCGENEFIT_MAX_CONSTRAINTS,
    redundancy: float = 0.01,
    return_scores: bool = True,
    workdir: str = ".",
) -> dict:
    """LP-based marker selection via scGeneFit.

    Only non-trivial piece here (vs a 3-line upstream call) is the
    ``max_constraints`` cap — caps LP row count so very large datasets
    still solve in bounded memory/time. Leave it at
    ``SCGENEFIT_MAX_CONSTRAINTS`` (=1000) unless you know why to raise it.

    ``method`` ∈ ``"centers" | "pairwise" | "pairwise_centers"``.
    ``return_scores=True`` writes the full LP-weight ranking.
    """
    try:
        import scanpy as sc
        import numpy as np
        import pandas as pd
        import scGeneFit.functions as gf
    except ImportError as e:
        return {"error": f"scGeneFit needs scanpy/pandas/scGeneFit. `pip install scGeneFit`. {e}"}

    _require_path(adata_path)
    out_dir = os.path.join(workdir, "gene_panels", "scgenefit")
    os.makedirs(out_dir, exist_ok=True)

    adata = sc.read_h5ad(adata_path)
    if label_key not in adata.obs.columns:
        return {"error": f"label_key '{label_key}' not found in adata.obs."}
    if getattr(adata, "isbacked", False):
        adata = adata.to_memory()

    # Cell → label vector (integer codes) and dense matrix scGeneFit expects.
    X_dense = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
    labels = adata.obs[label_key].astype(str).to_numpy()

    # Sampling to cap constraint count.
    n_cells = X_dense.shape[0]
    if method != "centers" and max_constraints and n_cells > max_constraints:
        rng = np.random.default_rng(0)
        idx = rng.choice(n_cells, size=max_constraints, replace=False)
        X_dense = X_dense[idx]
        labels = labels[idx]

    if method == "centers":
        markers = gf.get_markers(
            X_dense, labels, n_top_genes,
            method="centers", epsilon=epsilon_param,
            redundancy=redundancy,
        )
    elif method == "pairwise":
        markers = gf.get_markers(
            X_dense, labels, n_top_genes,
            method="pairwise", epsilon=epsilon_param,
            sampling_rate=sampling_rate, n_neighbors=n_neighbors,
            redundancy=redundancy,
        )
    elif method == "pairwise_centers":
        markers = gf.get_markers(
            X_dense, labels, n_top_genes,
            method="pairwise_centers", epsilon=epsilon_param,
            sampling_rate=sampling_rate, n_neighbors=n_neighbors,
            redundancy=redundancy,
        )
    else:
        return {"error": f"Unknown method '{method}'. Use centers|pairwise|pairwise_centers."}

    selected_genes = [adata.var_names[i] for i in markers]
    panel_path = os.path.join(out_dir, f"scgenefit_top_{n_top_genes}.csv")
    pd.DataFrame({"gene": selected_genes}).to_csv(panel_path, index=False)

    result = {
        "used_dataset": adata_path,
        "top_n": n_top_genes,
        "saved_to": {"panel": panel_path},
        "genes": selected_genes,
    }
    if return_scores:
        # scGeneFit emits rank order — synthesise a descending score series.
        ranked = [
            {"gene": g, "score": float(n_top_genes - i)}
            for i, g in enumerate(selected_genes)
        ]
        score_path = os.path.join(out_dir, "scgenefit_scores.csv")
        pd.DataFrame(ranked).to_csv(score_path, index=False)
        result["saved_to"]["scores"] = score_path
        result["genes"] = ranked
    return result


# ─── SpaPROS runtime estimator ──────────────────────────────────────────────


def estimate_spapros_runtime(
    adata_path: str,
    num_markers: int = 100,
    n_hvg: int = SPAPROS_N_HVG,
    warning_minutes: float = SPAPROS_RUNTIME_WARNING_MINUTES,
    skip_minutes: float = SPAPROS_RUNTIME_SKIP_MINUTES,
) -> dict[str, Any]:
    """Predict SpaPROS wall-clock from dataset shape — cheap metadata-only read.

    Returns a ``severity`` tier the caller can use to gate the real call
    behind a user confirmation prompt when the estimate is large.

    Heuristic: ``seconds ≈ (n_cells / 10_000) × (n_hvg_effective / 3_000) × 180
                            + (num_markers / 100) × 60``.
    """
    try:
        import anndata as ad
    except ImportError as e:
        return {"error": f"Needs anndata. `pip install anndata`. {e}"}

    _require_path(adata_path)
    adata = ad.read_h5ad(adata_path, backed="r")
    n_cells = int(adata.n_obs)
    n_genes_total = int(adata.n_vars)
    n_genes_effective = min(n_hvg, n_genes_total)

    seconds = (
        (n_cells / 10_000.0)
        * (n_genes_effective / 3_000.0)
        * _SPAPROS_SECS_PER_10K_CELLS_PER_3K_HVG
        + (num_markers / 100.0) * _SPAPROS_SECS_PER_100_MARKERS
    )
    minutes = seconds / 60.0

    if minutes < warning_minutes:
        severity = "fast"
    elif minutes < skip_minutes:
        severity = "slow"
    else:
        severity = "very_slow"

    return {
        "n_cells": n_cells,
        "n_genes_total": n_genes_total,
        "n_genes_effective": n_genes_effective,
        "estimated_seconds": round(seconds, 1),
        "estimated_minutes": round(minutes, 2),
        "severity": severity,
        "reason": (
            f"{n_cells:,} cells × {n_genes_effective} effective genes × SpaPROS ~"
            f"{_SPAPROS_SECS_PER_10K_CELLS_PER_3K_HVG:.0f}s per 10k×3k + "
            f"{num_markers} markers × {_SPAPROS_SECS_PER_100_MARKERS:.0f}s/100."
        ),
    }
