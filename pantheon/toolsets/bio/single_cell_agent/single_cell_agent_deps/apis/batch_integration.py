"""Batch integration wrapper around OmicVerse batch_correction"""

import os
from typing import Dict, Any, Optional


def get_batch_integration_for_dataset(
    adata_path: str,
    batch_key: str = "batch",
    method: str = "harmony",
    n_pcs: int = 50,
    save_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply batch correction and report summary. Requires a column like 'batch' in obs."""
    try:
        import scanpy as sc
        import matplotlib.pyplot as plt
        import omicverse as ov

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)
        if batch_key not in adata.obs.columns:
            return {"success": False, "error": f"Batch key '{batch_key}' not found in obs", "available_obs_keys": list(adata.obs.columns)}

        if "X_pca" not in adata.obsm:
            sc.pp.normalize_total(adata)
            sc.pp.log1p(adata)
            sc.tl.pca(adata, n_comps=min(n_pcs, 50))

        try:
            ov.single.batch_correction(adata, batch_key=batch_key, methods=method, n_pcs=n_pcs)
        except Exception as e:
            return {"success": False, "error": f"Batch correction failed: {e}"}

        rep_map = {
            "harmony": "X_harmony",
            "combat": "X_combat",
            "scanorama": "X_scanorama",
            "scVI": "X_scVI",
            "CellANOVA": "X_cellanova",
        }
        rep_key = rep_map.get(method, None)

        saved_plot = None
        if save_dir and rep_key and rep_key in adata.obsm:
            try:
                os.makedirs(save_dir, exist_ok=True)
                saved_plot = os.path.join(save_dir, f"batch_{method}_umap.png")
                sc.pp.neighbors(adata, use_rep=rep_key)
                sc.tl.umap(adata)
                sc.pl.umap(adata, color=[batch_key], show=False)
                plt.savefig(saved_plot, dpi=200, bbox_inches="tight")
                plt.close()
            except Exception:
                saved_plot = None

        return {
            "success": True,
            "method_used": method,
            "batch_key": batch_key,
            "num_batches": int(adata.obs[batch_key].nunique()),
            "embedding_key": rep_key,
            "saved_plot": saved_plot,
        }
    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install scanpy matplotlib omicverse"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_batch_integration_for_dataset_doc = {
    "name": "get_batch_integration_for_dataset",
    "description": "Apply batch correction (Harmony/Combat/etc.) and summarize",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "batch_key": {"type": "string"},
            "method": {"type": "string", "enum": ["harmony", "combat", "scanorama", "scVI", "CellANOVA"]},
            "n_pcs": {"type": "integer"},
            "save_dir": {"type": "string"},
        },
        "required": ["adata_path", "batch_key"],
    },
}

