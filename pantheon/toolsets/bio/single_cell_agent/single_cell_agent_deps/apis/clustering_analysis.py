"""Clustering analysis wrappers"""

import os
from typing import Dict, Any, Optional


def get_clustering_for_dataset(
    adata_path: str,
    method: str = "leiden",
    resolution: float = 1.0,
    save_dir: Optional[str] = None,
    key_added: Optional[str] = None,
) -> Dict[str, Any]:
    """Cluster cells using Leiden/Louvain and summarize results."""
    try:
        import scanpy as sc
        import matplotlib.pyplot as plt

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)

        if "X_pca" not in adata.obsm:
            sc.pp.normalize_total(adata)
            sc.pp.log1p(adata)
            sc.tl.pca(adata)
        if "neighbors" not in adata.uns:
            sc.pp.neighbors(adata)
        if "X_umap" not in adata.obsm:
            sc.tl.umap(adata)

        key = key_added or method
        if method == "leiden":
            sc.tl.leiden(adata, resolution=resolution, key_added=key)
        else:
            sc.tl.louvain(adata, resolution=resolution, key_added=key)

        counts = adata.obs[key].value_counts().sort_index()
        saved_plot = None
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            saved_plot = os.path.join(save_dir, f"umap_{key}.png")
            sc.pl.umap(adata, color=[key], show=False)
            plt.savefig(saved_plot, dpi=200, bbox_inches="tight")
            plt.close()

        return {
            "success": True,
            "method_used": method,
            "resolution": resolution,
            "num_clusters": int(counts.shape[0]),
            "cluster_sizes": counts.to_dict(),
            "saved_plot": saved_plot,
        }
    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install scanpy matplotlib"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_clustering_for_dataset_doc = {
    "name": "get_clustering_for_dataset",
    "description": "Cluster cells using Leiden/Louvain and summarize results",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "method": {"type": "string", "enum": ["leiden", "louvain"]},
            "resolution": {"type": "number"},
            "save_dir": {"type": "string"},
            "key_added": {"type": "string"},
        },
        "required": ["adata_path"],
    },
}

