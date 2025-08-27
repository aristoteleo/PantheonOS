"""QC and preprocessing summary using Scanpy/OmicVerse"""

import os
from typing import Dict, Any, Optional


def get_qc_and_preprocessing_for_dataset(
    adata_path: str,
    save_dir: Optional[str] = None,
    mt_prefix: str = "MT-",
    n_top_genes: int = 2000,
) -> Dict[str, Any]:
    """Compute basic QC metrics and minimal preprocessing (PCA/UMAP) for quick sanity checks."""
    try:
        import scanpy as sc
        import numpy as np
        import matplotlib.pyplot as plt

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)

        # Basic QC
        adata.obs["n_counts"] = adata.X.sum(axis=1).A1 if hasattr(adata.X, "A1") else adata.X.sum(axis=1)
        adata.obs["n_genes"] = (adata.X > 0).sum(axis=1).A1 if hasattr(adata.X, "A1") else (adata.X > 0).sum(axis=1)
        if "mt" not in adata.var.columns:
            adata.var["mt"] = adata.var_names.str.startswith(mt_prefix)
        sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)

        # Minimal preprocessing
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
        sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes, subset=True)
        sc.pp.scale(adata, max_value=10)
        sc.tl.pca(adata, n_comps=30)
        sc.pp.neighbors(adata, n_neighbors=15)
        sc.tl.umap(adata)

        saved_plot = None
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            saved_plot = os.path.join(save_dir, "qc_umap.png")
            sc.pl.umap(adata, color=["total_counts", "pct_counts_mt"], show=False)
            plt.savefig(saved_plot, dpi=200, bbox_inches="tight")
            plt.close()

        qc_stats = {
            "cells": int(adata.n_obs),
            "genes": int(adata.n_vars),
            "n_counts_mean": float(np.mean(adata.obs["total_counts"])),
            "n_genes_mean": float(np.mean(adata.obs["n_genes_by_counts"])),
            "pct_mt_mean": float(np.mean(adata.obs["pct_counts_mt"]))
        }

        return {
            "success": True,
            "qc": qc_stats,
            "preprocessing": {"hvg": n_top_genes, "pca_components": 30, "neighbors": 15, "embeddings": ["umap"]},
            "saved_plot": saved_plot,
        }

    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install scanpy matplotlib"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_qc_and_preprocessing_for_dataset_doc = {
    "name": "get_qc_and_preprocessing_for_dataset",
    "description": "Compute QC metrics and minimal preprocessing (PCA/UMAP)",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "save_dir": {"type": "string"},
            "mt_prefix": {"type": "string"},
            "n_top_genes": {"type": "integer"},
        },
        "required": ["adata_path"],
    },
}

