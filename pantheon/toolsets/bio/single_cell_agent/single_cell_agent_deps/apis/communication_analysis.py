"""Cell-cell communication wrapper (CellPhoneDB via OmicVerse)"""

import os
from typing import Dict, Any, Optional


def get_cell_communication_for_dataset(
    adata_path: str,
    celltype_key: str = "cell_type",
    save_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a lightweight cell-cell communication summary using OmicVerse CPDB helpers."""
    try:
        import scanpy as sc
        import omicverse as ov

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)
        if celltype_key not in adata.obs.columns:
            fallback = "leiden" if "leiden" in adata.obs.columns else None
            if not fallback:
                return {"success": False, "error": f"'{celltype_key}' not in obs and no fallback label found", "available_obs_keys": list(adata.obs.columns)}
            celltype_key = fallback

        try:
            res = ov.single.cellphonedb_v5(adata, celltype_key=celltype_key, iterations=100, threads=2, debug=False)
            summary = {
                "success": True,
                "method_used": "CellPhoneDB_v5",
                "celltype_key": celltype_key,
                "cells": int(adata.n_obs),
                "celltypes": int(adata.obs[celltype_key].nunique()),
            }
            return summary if isinstance(res, dict) else summary
        except Exception as e:
            return {"success": False, "error": f"Communication analysis not available in this environment: {e}", "suggestion": "Install ktplotspy and cellphonedb; ensure CPDB DB is accessible"}
    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install omicverse scanpy"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_cell_communication_for_dataset_doc = {
    "name": "get_cell_communication_for_dataset",
    "description": "Run a lightweight cell-cell communication summary (CellPhoneDB via OmicVerse)",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "celltype_key": {"type": "string"},
            "save_dir": {"type": "string"},
        },
        "required": ["adata_path"],
    },
}

