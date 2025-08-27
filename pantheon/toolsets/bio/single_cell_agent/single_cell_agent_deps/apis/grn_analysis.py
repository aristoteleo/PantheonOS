"""GRN/TF activity wrapper (SCENIC/AUCell via OmicVerse)"""

import os
from typing import Dict, Any, Optional


def get_grn_analysis_for_dataset(
    adata_path: str,
    method: str = "SCENIC",
    species: str = "human",
    save_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Attempt GRN/TF activity analysis; guide user if heavy deps missing."""
    try:
        import scanpy as sc
        import omicverse as ov

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)

        try:
            if method.upper() == "SCENIC":
                scenic = ov.single.SCENIC(adata, species=species)
                scenic.build()
                summary = {"success": True, "method_used": "SCENIC", "cells": int(adata.n_obs), "genes": int(adata.n_vars)}
            else:
                ov.single.aucell(adata)
                summary = {"success": True, "method_used": "AUCell", "cells": int(adata.n_obs)}
            return summary
        except Exception as e:
            return {"success": False, "error": f"GRN analysis not available in this environment: {e}", "suggestion": "Install pySCENIC resources and required databases; consider running offline"}
    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install omicverse scanpy"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_grn_analysis_for_dataset_doc = {
    "name": "get_grn_analysis_for_dataset",
    "description": "Attempt GRN/TF activity analysis (SCENIC/AUCell) with safe fallbacks",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "method": {"type": "string", "enum": ["SCENIC", "AUCell"]},
            "species": {"type": "string", "enum": ["human", "mouse"]},
            "save_dir": {"type": "string"},
        },
        "required": ["adata_path"],
    },
}

