"""
Gene panel selection algorithms — plain Python library.

Three selection methods:

- :func:`select_spapros` — probe-set selection (SpaPROS)
- :func:`select_random_forest` — Gini importance ranking
- :func:`select_scgenefit` — LP-based marker selection

All three are synchronous functions designed to be called from the
agent's notebook / Python sandbox. They are intentionally *not*
wrapped as ``@tool`` methods: they do not need their own ToolSet
registration (see PR #48 review N7).

Each function can accept an optional :class:`GenePanelConfig` to tune
algorithm caps (LP constraints, HVG pre-filter size, RF tree count).
When ``config`` is omitted, defaults are loaded from project
``settings.json`` (section ``gene_panel``).
"""

from __future__ import annotations

import os
from typing import Optional

from pantheon.toolsets.gene_panel.config import GenePanelConfig
from pantheon.utils.log import logger


def _resolve_config(config: Optional[GenePanelConfig]) -> GenePanelConfig:
    return config if config is not None else GenePanelConfig.from_settings()


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


__all__ = ["select_spapros", "select_random_forest", "select_scgenefit"]
