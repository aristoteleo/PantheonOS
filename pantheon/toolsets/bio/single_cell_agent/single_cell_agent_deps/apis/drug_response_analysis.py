"""Drug response prediction wrapper (scDrug via OmicVerse)"""

import os
from typing import Dict, Any, Optional


def get_drug_response_for_dataset(
    adata_path: str,
    model: str = "GDSC",
    save_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Provide guidance or attempt scDrug-like predictions if environment is ready."""
    try:
        import scanpy as sc
        import omicverse as ov  # noqa: F401 - imported to signal dependency

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)

        return {
            "success": False,
            "error": "Drug response models not configured",
            "suggestion": "Configure CaDRReS-Sc scripts and pretrained models; then call ov.single.Drug_Response",
            "cells": int(adata.n_obs),
            "genes": int(adata.n_vars),
            "model": model,
        }
    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install omicverse scanpy"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_drug_response_for_dataset_doc = {
    "name": "get_drug_response_for_dataset",
    "description": "Provide guidance or run scDrug predictions if configured",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "model": {"type": "string", "enum": ["GDSC", "PRISM"]},
            "save_dir": {"type": "string"},
        },
        "required": ["adata_path"],
    },
}

