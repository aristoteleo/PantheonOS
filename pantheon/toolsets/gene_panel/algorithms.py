"""
Gene panel selection algorithms.

Pure synchronous Python functions exposing the three advanced selection
methods used in the GPS workflow:

* :func:`select_spapros`        — SpaPROS probe-set selection
* :func:`select_random_forest`  — Random Forest feature importance ranking
* :func:`select_scgenefit`      — scGeneFit LP-based marker selection

HVG and DE are intentionally out of scope here: they are one-liners in
scanpy and the agent runs them directly in notebook cells.

Design notes
------------
* **Not a ToolSet.** These are plain library functions meant to be called
  from inside a notebook cell via the agent's general-purpose
  ``integrated_notebook`` / ``python_interpreter`` toolsets. This avoids
  a bespoke toolset registration (see PR #48 review).
* **Lazy, optional imports.** Heavy scientific dependencies (scanpy,
  spapros, scGeneFit, sklearn) are imported inside each function. Missing
  packages raise a clear :class:`ImportError` pointing at what to install.
* **Native types.** Parameters are proper ``int``/``float``/``bool`` —
  no string coercion. Callers pass real Python values.
* **Exceptions propagate.** Validation failures raise ``ValueError``;
  downstream errors bubble up with their full traceback. This gives the
  LLM running the notebook an actionable error rather than an opaque
  ``{"error": "..."}`` payload.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger


# --------------------------------------------------------------------- #
#  Internal helpers                                                     #
# --------------------------------------------------------------------- #


def _require_path(adata_path: str | None) -> str:
    if not adata_path:
        raise ValueError("adata_path is required (no default configured).")
    if not os.path.isfile(adata_path):
        raise FileNotFoundError(f"adata file not found: {adata_path}")
    return adata_path


def _ensure_workdir(workdir: str | None, subdir: str) -> str:
    base = workdir or "."
    out_dir = os.path.join(base, "gene_panels", subdir)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return out_dir


def _require_label_key(adata, label_key: str) -> None:
    if not label_key or label_key not in adata.obs.columns:
        raise ValueError(
            f"label_key {label_key!r} not found in adata.obs "
            f"(available: {list(adata.obs.columns)[:10]}...)"
        )


def _load_adata(path: str):
    """Load ``.h5ad`` into memory, materialising backed objects."""
    try:
        import scanpy as sc
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "scanpy is required for gene panel selection. "
            "Install with: pip install scanpy"
        ) from e

    adata = sc.read_h5ad(path)
    if getattr(adata, "isbacked", False):
        adata = adata.to_memory()
    return adata


# --------------------------------------------------------------------- #
#  SpaPROS                                                              #
# --------------------------------------------------------------------- #


def select_spapros(
    adata_path: str,
    label_key: str,
    num_markers: int = 100,
    n_hvg: int = 3000,
    return_scores: bool = False,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Select marker genes using SpaPROS probe-set selection.

    Parameters
    ----------
    adata_path
        Path to a ``.h5ad`` dataset.
    label_key
        Column in ``adata.obs`` identifying cell groups.
    num_markers
        Number of markers to return in the final panel.
    n_hvg
        HVG pre-filter size before handing the matrix to SpaPROS.
    return_scores
        If ``True``, also return per-gene importance scores.
    workdir
        Output directory root (results written to
        ``{workdir}/gene_panels/spapros/``).

    Returns
    -------
    dict
        Keys: ``used_dataset``, ``top_n``, ``saved_to``, ``genes``.
    """
    # Validate args before importing heavy deps (surfaces ValueError cleanly)
    path = _require_path(adata_path)
    out_dir = _ensure_workdir(workdir, "spapros")

    try:
        import scanpy as sc
        import pandas as pd
        import numpy as np
        import spapros as sp
    except ImportError as e:
        raise ImportError(
            "SpaPROS requires spapros, scanpy, pandas, numpy. "
            "Install with: pip install spapros scanpy"
        ) from e

    adata = _load_adata(path)

    # Pre-filter to HVGs so SpaPROS stays tractable
    sc.pp.highly_variable_genes(adata, flavor="cell_ranger", n_top_genes=n_hvg)
    adata = adata[:, adata.var["highly_variable"]]

    _require_label_key(adata, label_key)

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

    selected = df[df["selection"] == True].index.tolist()  # noqa: E712
    panel_path = os.path.join(out_dir, f"spapros_top_{num_markers}.csv")
    pd.DataFrame({"gene": selected}).to_csv(panel_path, index=False)

    if return_scores and "importance_score" in df.columns:
        scores = [
            {"gene": g, "score": float(row.get("importance_score", np.nan))}
            for g, row in df.iterrows()
        ]
        score_path = os.path.join(out_dir, "spapros_scores.csv")
        pd.DataFrame(scores).to_csv(score_path, index=False)
        return {
            "used_dataset": path,
            "top_n": num_markers,
            "saved_to": {
                "panel": panel_path,
                "full_table": full_path,
                "scores": score_path,
            },
            "genes": scores,
        }

    return {
        "used_dataset": path,
        "top_n": num_markers,
        "saved_to": {"panel": panel_path, "full_table": full_path},
        "genes": selected,
    }


# --------------------------------------------------------------------- #
#  Random Forest                                                        #
# --------------------------------------------------------------------- #


def select_random_forest(
    adata_path: str,
    label_key: str,
    n_top_genes: int = 1000,
    n_estimators: int = 300,
    return_scores: bool = False,
    random_state: int = 42,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Rank genes by Random Forest feature importance.

    Trains an RF classifier on ``adata.X`` to predict ``label_key`` and
    returns genes ranked by Gini importance.

    Parameters
    ----------
    adata_path
        Path to a ``.h5ad`` dataset.
    label_key
        Column in ``adata.obs`` identifying cell labels.
    n_top_genes
        Number of top-ranked genes to save to disk.
    n_estimators
        Number of trees in the forest.
    return_scores
        If ``True``, return every gene with its score.
    random_state
        RNG seed for reproducibility.
    workdir
        Output directory root.

    Returns
    -------
    dict
        Keys: ``used_dataset``, ``top_n``, ``saved_to``, ``genes``.
    """
    path = _require_path(adata_path)
    out_dir = _ensure_workdir(workdir, "random_forest")

    try:
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import RandomForestClassifier
    except ImportError as e:
        raise ImportError(
            "Random Forest selection requires scikit-learn, numpy, pandas. "
            "Install with: pip install scikit-learn"
        ) from e

    adata = _load_adata(path)
    _require_label_key(adata, label_key)

    X = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
    y = adata.obs[label_key].astype("category").cat.codes.values

    clf = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=random_state,
        n_jobs=-1,
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

    save_path = os.path.join(out_dir, f"rf_top_{n_top_genes}.csv")
    pd.DataFrame(ranked[:n_top_genes]).to_csv(save_path, index=False)

    return {
        "used_dataset": path,
        "top_n": n_top_genes,
        "saved_to": save_path,
        "genes": ranked if return_scores else [r["gene"] for r in ranked[:n_top_genes]],
    }


# --------------------------------------------------------------------- #
#  scGeneFit                                                            #
# --------------------------------------------------------------------- #


def select_scgenefit(
    adata_path: str,
    label_key: str,
    n_top_genes: int = 200,
    method: str = "centers",
    epsilon_param: float = 1.0,
    sampling_rate: float = 1.0,
    n_neighbors: int = 3,
    max_constraints: int = 1000,
    redundancy: float = 0.01,
    return_scores: bool = False,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Select marker genes via scGeneFit (LP-based marker selection).

    Solves a linear program that finds a sparse weight vector over genes
    such that labeled cell groups remain separable. The LP weights double
    as per-gene importance scores.

    Parameters
    ----------
    adata_path
        Path to a ``.h5ad`` dataset.
    label_key
        Column in ``adata.obs`` identifying cell labels.
    n_top_genes
        Number of markers to select.
    method
        Constraint strategy: ``"centers"``, ``"pairwise"``, or
        ``"pairwise_centers"``.
    epsilon_param
        LP epsilon scaling factor.
    sampling_rate
        Fraction of cells to sample for pairwise methods.
    n_neighbors
        Neighbours for pairwise constraint building.
    max_constraints
        Hard cap on constraint rows (keeps the LP tractable; recommend
        ``<= 1000``).
    redundancy
        Redundancy param for center summarisation.
    return_scores
        If ``True``, return every gene with its LP weight.
    workdir
        Output directory root.

    Returns
    -------
    dict
        Keys: ``used_dataset``, ``top_n``, ``saved_to``, ``genes``.
    """
    path = _require_path(adata_path)
    out_dir = _ensure_workdir(workdir, "scgenefit")

    try:
        import numpy as np
        import pandas as pd
        import scipy.sparse as sps
        import scGeneFit.functions as gf
    except ImportError as e:
        raise ImportError(
            "scGeneFit selection requires scGeneFit, numpy, pandas, scipy. "
            "Install with: pip install scGeneFit"
        ) from e

    logger.info(f"scGeneFit: loading {path}")
    adata = _load_adata(path)
    _require_label_key(adata, label_key)

    logger.info(
        f"scGeneFit: shape={adata.shape}, method={method}, "
        f"n_top_genes={n_top_genes}, max_constraints={max_constraints}"
    )

    # LP solver needs dense data
    X = adata.X.toarray() if sps.issparse(adata.X) else np.asarray(adata.X)
    y = adata.obs[label_key].astype("category").values
    d = X.shape[1]

    # scGeneFit exposes these helpers under dunder-prefixed names
    _sample = getattr(gf, "__sample")
    _pairwise = getattr(gf, "__select_constraints_pairwise")
    _pairwise_cent = getattr(gf, "__select_constraints_centers")
    _summarised = getattr(gf, "__select_constraints_summarized")
    _lp_markers = getattr(gf, "__lp_markers")

    t0 = time.time()
    samples, samples_labels, _ = _sample(X, y, sampling_rate)
    logger.info(f"scGeneFit: sampled {len(samples)} cells in {time.time() - t0:.1f}s")

    t0 = time.time()
    if method == "pairwise_centers":
        constraints, smallest_norm = _pairwise_cent(X, y, samples, samples_labels)
    elif method == "pairwise":
        constraints, smallest_norm = _pairwise(
            X, y, samples, samples_labels, n_neighbors
        )
    else:
        constraints, smallest_norm = _summarised(X, y, redundancy)
    logger.info(
        f"scGeneFit: {constraints.shape[0]} constraints built in {time.time() - t0:.1f}s"
    )

    if constraints.shape[0] > max_constraints:
        rng = np.random.default_rng(42)
        idx = rng.permutation(constraints.shape[0])[:max_constraints]
        constraints = constraints[idx, :]
        logger.info(f"scGeneFit: capped to {max_constraints} constraints")

    t0 = time.time()
    sol = _lp_markers(constraints, n_top_genes, smallest_norm * epsilon_param)
    logger.info(f"scGeneFit: LP solved in {time.time() - t0:.1f}s")

    weights = np.asarray(sol["x"][:d], dtype=float)

    if return_scores:
        ranked = sorted(
            (
                {"gene": g, "score": float(s)}
                for g, s in zip(adata.var_names, weights)
            ),
            key=lambda d: d["score"],
            reverse=True,
        )
        save_path = os.path.join(out_dir, "scgenefit_scores.csv")
        pd.DataFrame(ranked).to_csv(save_path, index=False)
        return {
            "used_dataset": path,
            "top_n": len(ranked),
            "saved_to": save_path,
            "genes": ranked,
        }

    order = np.argsort(-weights)[:n_top_genes]
    top = adata.var_names[order].tolist()
    save_path = os.path.join(out_dir, f"scgenefit_top_{n_top_genes}.csv")
    pd.DataFrame({"gene": top}).to_csv(save_path, index=False)

    return {
        "used_dataset": path,
        "top_n": n_top_genes,
        "saved_to": save_path,
        "genes": top,
    }


# --------------------------------------------------------------------- #
#  SpaPROS runtime estimator                                            #
# --------------------------------------------------------------------- #


# Heuristic calibration constants. These are intentionally rough —
# SpaPROS runtime depends on the dataset's biological structure as well
# as raw dimensions. Tune via PRs if empirical drift is observed.
_SPAPROS_BASE_SECONDS = 60.0
_SPAPROS_SECS_PER_10K_CELLS_PER_3K_HVG = 180.0
_SPAPROS_SECS_PER_100_MARKERS = 60.0


def estimate_spapros_runtime(
    adata_path: str,
    num_markers: int = 100,
    n_hvg: int = 3000,
    warning_minutes: float = 5.0,
    skip_minutes: float = 30.0,
) -> dict[str, Any]:
    """Estimate SpaPROS wall-clock runtime before committing to the full run.

    SpaPROS is often the slow leg of the algorithmic selection pipeline:
    on large or high-dimensional datasets it can run for tens of minutes
    to hours. This helper gives the leader a cheap (metadata-only) peek
    so it can gate the call behind a user confirmation via ``notify_user``.

    The cost model is a simple heuristic calibrated against typical
    runtimes — it is **not** a guarantee. Treat the returned estimate as
    the right order of magnitude, not a precise prediction.

    Parameters
    ----------
    adata_path
        Path to a ``.h5ad`` dataset. Only ``shape`` / metadata are read.
    num_markers
        Target panel size that will be passed to SpaPROS.
    n_hvg
        HVG pre-filter size that will be passed to SpaPROS.
    warning_minutes
        Threshold separating ``"fast"`` from ``"slow"`` severity.
    skip_minutes
        Threshold separating ``"slow"`` from ``"very_slow"`` severity.

    Returns
    -------
    dict
        ``n_cells``, ``n_genes_total``, ``n_genes_effective`` (min of
        ``n_hvg`` and total genes), ``estimated_seconds``,
        ``estimated_minutes``, ``severity`` (one of ``"fast"``,
        ``"slow"``, ``"very_slow"``), and ``reason`` — a short,
        user-facing explanation the leader can relay via ``notify_user``.
    """
    path = _require_path(adata_path)

    try:
        import anndata as ad
    except ImportError as e:
        raise ImportError(
            "anndata is required for runtime estimation. "
            "Install with: pip install anndata"
        ) from e

    # Metadata-only read — does not materialise the expression matrix
    adata = ad.read_h5ad(path, backed="r")
    try:
        n_cells, n_genes_total = map(int, adata.shape)
    finally:
        # Release the HDF5 file handle
        file = getattr(adata, "file", None)
        if file is not None:
            try:
                file.close()
            except Exception:
                pass

    n_genes_effective = min(int(n_hvg), n_genes_total)

    seconds = (
        _SPAPROS_BASE_SECONDS
        + (n_cells / 10_000)
        * (n_genes_effective / 3_000)
        * _SPAPROS_SECS_PER_10K_CELLS_PER_3K_HVG
        + (num_markers / 100) * _SPAPROS_SECS_PER_100_MARKERS
    )
    minutes = seconds / 60.0

    if minutes >= skip_minutes:
        severity = "very_slow"
    elif minutes >= warning_minutes:
        severity = "slow"
    else:
        severity = "fast"

    reason = (
        f"~{minutes:.1f} min estimated for SpaPROS on "
        f"{n_cells:,} cells × {n_genes_effective:,} HVGs "
        f"(targeting {num_markers} markers). "
        f"Severity: {severity} "
        f"(warning ≥ {warning_minutes:g} min, skip ≥ {skip_minutes:g} min)."
    )

    return {
        "n_cells": n_cells,
        "n_genes_total": n_genes_total,
        "n_genes_effective": n_genes_effective,
        "estimated_seconds": round(seconds, 1),
        "estimated_minutes": round(minutes, 1),
        "severity": severity,
        "reason": reason,
    }


__all__ = [
    "select_spapros",
    "select_random_forest",
    "select_scgenefit",
    "estimate_spapros_runtime",
]
