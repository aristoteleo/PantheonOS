from __future__ import annotations

import os
from typing import Any, Optional

from pantheon.toolsets.gene_panel.config import GenePanelConfig
from pantheon.utils.log import logger


def _resolve_config(config: Optional[GenePanelConfig]) -> GenePanelConfig:
    return config if config is not None else GenePanelConfig.from_settings()


def _require_path(adata_path: Optional[str]) -> str:
    if not adata_path:
        raise ValueError("adata_path is required (no default configured).")
    if not os.path.isfile(adata_path):
        raise FileNotFoundError(f"adata file not found: {adata_path}")
    return adata_path


# ---------------------------------------------------------------------- #
#  SpaPROS                                                                #
# ---------------------------------------------------------------------- #


def select_spapros(
    adata_path: str,
    label_key: str,
    num_markers: int = 100,
    n_hvg: Optional[int] = None,
    return_scores: bool = False,
    workdir: str = ".",
    config: Optional[GenePanelConfig] = None,
) -> dict:
    """Select marker genes using SpaPROS probe-set selection.

    Args:
        adata_path: Path to .h5ad dataset.
        label_key: Column in ``.obs`` for cell groups.
        num_markers: Number of markers to select.
        n_hvg: HVG pre-filter size. Defaults to
            ``settings.gene_panel.spapros_n_hvg`` (3000).
        return_scores: Include per-gene importance scores in the result.
        workdir: Output directory.
        config: Override for algorithm caps. Loaded from settings if None.

    Returns:
        dict with keys: ``used_dataset``, ``top_n``, ``saved_to``, ``genes``.
    """
    cfg = _resolve_config(config)
    if n_hvg is None:
        n_hvg = cfg.spapros_n_hvg

    try:
        import scanpy as sc
        import pandas as pd
        import numpy as np
        import spapros as sp
    except ImportError as e:
        return {
            "error": (
                "SpaPROS requires scanpy, pandas, numpy, spapros. "
                "Install with: pip install scanpy spapros. "
                f"Details: {e}"
            )
        }

    try:
        if not adata_path:
            return {"error": "No dataset path provided."}

        out_dir = os.path.join(workdir, "gene_panels", "spapros")
        os.makedirs(out_dir, exist_ok=True)

        adata = sc.read_h5ad(adata_path)

        sc.pp.highly_variable_genes(adata, flavor="cell_ranger", n_top_genes=n_hvg)
        adata = adata[:, adata.var["highly_variable"]]

        if not label_key or label_key not in adata.obs.columns:
            return {"error": f"label_key '{label_key}' not found in adata.obs."}

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

        if return_scores and "importance_score" in df.columns:
            score_list = [
                {"gene": g, "score": float(row.get("importance_score", np.nan))}
                for g, row in df.iterrows()
            ]
            score_path = os.path.join(out_dir, "spapros_scores.csv")
            pd.DataFrame(score_list).to_csv(score_path, index=False)
            return {
                "used_dataset": adata_path,
                "top_n": num_markers,
                "saved_to": {
                    "panel": panel_path,
                    "full_table": full_path,
                    "scores": score_path,
                },
                "genes": score_list,
            }

        return {
            "used_dataset": adata_path,
            "top_n": num_markers,
            "saved_to": {"panel": panel_path, "full_table": full_path},
            "genes": selected,
        }

    except Exception as e:
        import traceback

        logger.error(f"select_spapros failed: {e}\n{traceback.format_exc()}")
        return {"error": f"SpaPROS failed: {e}"}


# ---------------------------------------------------------------------- #
#  Random Forest                                                          #
# ---------------------------------------------------------------------- #


def select_random_forest(
    adata_path: str,
    label_key: str,
    n_top_genes: int = 1000,
    return_scores: bool = False,
    random_state: int = 42,
    n_estimators: Optional[int] = None,
    workdir: str = ".",
    config: Optional[GenePanelConfig] = None,
) -> dict:
    """Rank genes by Random Forest feature importance (Gini).

    Trains an RF classifier on the expression matrix to predict cell
    labels. With ``return_scores=True`` the full ranking is returned —
    call once, slice top-K in pandas for different panel sizes.

    Args:
        adata_path: Path to .h5ad dataset.
        label_key: Column in ``.obs`` for cell labels.
        n_top_genes: How many top genes to save to the panel CSV.
        return_scores: Return all genes with importance scores.
        random_state: Random seed.
        n_estimators: Number of trees. Defaults to
            ``settings.gene_panel.rf_n_estimators`` (300).
        workdir: Output directory.
        config: Override for algorithm caps. Loaded from settings if None.

    Returns:
        dict with keys: ``used_dataset``, ``top_n``, ``saved_to``, ``genes``.
    """
    cfg = _resolve_config(config)
    if n_estimators is None:
        n_estimators = cfg.rf_n_estimators

    try:
        import scanpy as sc
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import RandomForestClassifier
    except ImportError as e:
        return {
            "error": (
                "Random Forest selection requires scanpy, numpy, pandas, scikit-learn. "
                "Install with: pip install scanpy scikit-learn. "
                f"Details: {e}"
            )
        }

    try:
        if not adata_path:
            return {"error": "No dataset path provided."}

        out_dir = os.path.join(workdir, "gene_panels", "random_forest")
        os.makedirs(out_dir, exist_ok=True)

        adata = sc.read_h5ad(adata_path)

        if not label_key or label_key not in adata.obs.columns:
            return {"error": f"label_key '{label_key}' not found in adata.obs."}

        X = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
        y = adata.obs[label_key].astype("category").cat.codes.values

        clf = RandomForestClassifier(
            n_estimators=n_estimators, random_state=random_state, n_jobs=-1
        )
        clf.fit(X, y)

        ranked = sorted(
            [
                {"gene": g, "score": float(s)}
                for g, s in zip(adata.var_names, clf.feature_importances_)
            ],
            key=lambda d: d["score"],
            reverse=True,
        )

        save_path = os.path.join(out_dir, f"rf_top_{n_top_genes}.csv")
        pd.DataFrame(ranked[:n_top_genes]).to_csv(save_path, index=False)

        return {
            "used_dataset": adata_path,
            "top_n": n_top_genes,
            "saved_to": save_path,
            "genes": ranked if return_scores else [x["gene"] for x in ranked[:n_top_genes]],
        }

    except Exception as e:
        logger.error(f"select_random_forest failed: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------- #
#  scGeneFit                                                              #
# ---------------------------------------------------------------------- #


def select_scgenefit(
    adata_path: str,
    label_key: str,
    n_top_genes: int = 200,
    method: str = "centers",
    epsilon_param: float = 1.0,
    sampling_rate: float = 1.0,
    n_neighbors: int = 3,
    max_constraints: Optional[int] = None,
    redundancy: float = 0.01,
    return_scores: bool = False,
    workdir: str = ".",
    config: Optional[GenePanelConfig] = None,
) -> dict:
    """Select marker genes via scGeneFit (LP-based marker selection).

    Solves a linear program that finds a sparse weight vector over
    genes such that labelled cell groups remain separable. Weights
    serve as per-gene importance scores.

    With ``return_scores=True`` the full ranked CSV is written — one
    call suffices for any panel size: slice top-K from the CSV in
    pandas. No need to re-run the LP for each K.

    Args:
        adata_path: Path to .h5ad dataset.
        label_key: Column in ``.obs`` for cell labels.
        n_top_genes: Number of markers to select when ``return_scores=False``.
        method: Constraint strategy — ``"centers"`` | ``"pairwise"`` |
            ``"pairwise_centers"``.
        epsilon_param: LP epsilon scaling factor.
        sampling_rate: Fraction of cells to sample for pairwise methods.
        n_neighbors: Neighbours for pairwise constraint building.
        max_constraints: Hard cap on constraint rows. Defaults to
            ``settings.gene_panel.scgenefit_max_constraints`` (1000).
        redundancy: Redundancy parameter for centre summarisation.
        return_scores: Return all genes with LP weights.
        workdir: Output directory.
        config: Override for algorithm caps. Loaded from settings if None.

    Returns:
        dict with keys: ``used_dataset``, ``top_n``, ``saved_to``, ``genes``.
    """
    cfg = _resolve_config(config)
    if max_constraints is None:
        max_constraints = cfg.scgenefit_max_constraints

    try:
        import scanpy as sc
        import numpy as np
        import pandas as pd
        import scipy.sparse as sps
        import scGeneFit.functions as gf
    except ImportError as e:
        return {
            "error": (
                "scGeneFit selection requires scanpy, numpy, pandas, scipy, scGeneFit. "
                "Install with: pip install scanpy scGeneFit. "
                f"Details: {e}"
            )
        }

    try:
        import time

        if not adata_path:
            return {"error": "No dataset path provided."}

        out_dir = os.path.join(workdir, "gene_panels", "scgenefit")
        os.makedirs(out_dir, exist_ok=True)

        logger.info(f"scGeneFit: loading {adata_path}")
        adata = sc.read_h5ad(adata_path)
        if getattr(adata, "isbacked", False):
            adata = adata.to_memory()

        if not label_key or label_key not in adata.obs.columns:
            return {"error": f"label_key '{label_key}' not found in adata.obs."}

        logger.info(
            f"scGeneFit: {adata.shape}, method={method}, "
            f"n_top_genes={n_top_genes}, max_constraints={max_constraints}"
        )

        if sps.issparse(adata.X):
            X = adata.X.toarray()
        else:
            X = np.asarray(adata.X)

        y = adata.obs[label_key].astype("category").values
        d = X.shape[1]

        _sample = getattr(gf, "__sample")
        _pairwise = getattr(gf, "__select_constraints_pairwise")
        _pairwise_cent = getattr(gf, "__select_constraints_centers")
        _summarised = getattr(gf, "__select_constraints_summarized")
        _lp_markers = getattr(gf, "__lp_markers")

        t0 = time.time()
        samples, samples_labels, _ = _sample(X, y, sampling_rate)
        logger.info(
            f"scGeneFit: sampled {len(samples)} cells in {time.time() - t0:.1f}s"
        )

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
            f"scGeneFit: {constraints.shape[0]} constraints built "
            f"in {time.time() - t0:.1f}s"
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
                [
                    {"gene": g, "score": float(s)}
                    for g, s in zip(adata.var_names, weights)
                ],
                key=lambda d: d["score"],
                reverse=True,
            )
            save_path = os.path.join(out_dir, "scgenefit_scores.csv")
            pd.DataFrame(ranked).to_csv(save_path, index=False)
            return {
                "used_dataset": adata_path,
                "top_n": len(ranked),
                "saved_to": save_path,
                "genes": ranked,
            }

        order = np.argsort(-weights)[:n_top_genes]
        top = adata.var_names[order].tolist()
        save_path = os.path.join(out_dir, f"scgenefit_top_{n_top_genes}.csv")
        pd.DataFrame({"gene": top}).to_csv(save_path, index=False)

        return {
            "used_dataset": adata_path,
            "top_n": n_top_genes,
            "saved_to": save_path,
            "genes": top,
        }

    except Exception as e:
        import traceback

        logger.error(f"scGeneFit failed: {e}\n{traceback.format_exc()}")
        return {"error": str(e)}


# ---------------------------------------------------------------------- #
#  SpaPROS runtime estimator                                              #
# ---------------------------------------------------------------------- #


_SPAPROS_BASE_SECONDS = 60.0
_SPAPROS_SECS_PER_10K_CELLS_PER_3K_HVG = 180.0
_SPAPROS_SECS_PER_100_MARKERS = 60.0


def estimate_spapros_runtime(
    adata_path: str,
    num_markers: int = 100,
    n_hvg: Optional[int] = None,
    warning_minutes: Optional[float] = None,
    skip_minutes: Optional[float] = None,
    config: Optional[GenePanelConfig] = None,
) -> dict[str, Any]:
    """Estimate SpaPROS wall-clock runtime from dataset metadata alone.

    Cheap pre-check the leader can run before committing to a full
    SpaPROS call. Returns a severity tier so the leader can gate the
    call behind a ``notify_user`` Run/Skip choice when the estimate is
    large.

    Args:
        adata_path: Path to .h5ad dataset (metadata-only read).
        num_markers: Target panel size for SpaPROS.
        n_hvg: HVG pre-filter size. Defaults to
            ``settings.gene_panel.spapros_n_hvg`` (3000).
        warning_minutes: "fast" → "slow" threshold. Defaults to
            ``settings.gene_panel.spapros_runtime_warning_minutes`` (5.0).
        skip_minutes: "slow" → "very_slow" threshold. Defaults to
            ``settings.gene_panel.spapros_runtime_skip_minutes`` (30.0).
        config: Override for defaults. Loaded from settings if None.

    Returns:
        dict with keys: ``n_cells``, ``n_genes_total``,
        ``n_genes_effective``, ``estimated_seconds``,
        ``estimated_minutes``, ``severity`` (``"fast"|"slow"|"very_slow"``),
        ``reason``.
    """
    cfg = _resolve_config(config)
    if n_hvg is None:
        n_hvg = cfg.spapros_n_hvg
    if warning_minutes is None:
        warning_minutes = cfg.spapros_runtime_warning_minutes
    if skip_minutes is None:
        skip_minutes = cfg.spapros_runtime_skip_minutes

    path = _require_path(adata_path)

    try:
        import anndata as ad
    except ImportError as e:
        raise ImportError(
            "anndata is required for runtime estimation. "
            "Install with: pip install anndata"
        ) from e

    adata = ad.read_h5ad(path, backed="r")
    try:
        n_cells, n_genes_total = map(int, adata.shape)
    finally:
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
