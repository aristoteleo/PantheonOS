"""Metacell analysis wrapper (SEACells/MetaCell via OmicVerse)"""

import os
from typing import Dict, Any, Optional


def get_metacell_analysis_for_dataset(
    adata_path: str,
    save_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Attempt to construct metacells and summarize counts."""
    try:
        import scanpy as sc
        import omicverse as ov

        if not os.path.exists(adata_path):
            return {"success": False, "error": f"File not found: {adata_path}"}

        adata = sc.read_h5ad(adata_path)

        try:
            meta = ov.single.MetaCell(adata)
            meta.build()
            n_meta = int(meta.metacell.shape[0]) if hasattr(meta, "metacell") else None
            return {"success": True, "metacells": n_meta}
        except Exception as e:
            return {"success": False, "error": f"Metacell analysis not available in this environment: {e}", "suggestion": "Check SEACells/MetaCell deps; consider running on a full environment"}
    except ImportError as e:
        return {"success": False, "error": f"Missing dependency: {e}", "suggestion": "pip install omicverse scanpy"}
    except Exception as e:
        return {"success": False, "error": str(e)}


get_metacell_analysis_for_dataset_doc = {
    "name": "get_metacell_analysis_for_dataset",
    "description": "Attempt metacell construction and summarize",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string"},
            "save_dir": {"type": "string"},
        },
        "required": ["adata_path"],
    },
}

