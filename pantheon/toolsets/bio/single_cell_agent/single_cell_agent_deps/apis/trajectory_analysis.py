"""Trajectory and pseudotime analysis using omicverse"""

import os
from typing import Dict, Any, Optional


def get_trajectory_analysis_for_dataset(adata_path: str, method: str = "TrajInfer", root_cell: Optional[str] = None) -> Dict[str, Any]:
    """Perform trajectory inference and pseudotime analysis"""
    try:
        import scanpy as sc
        import omicverse as ov
        import numpy as np

        # Load data
        if not os.path.exists(adata_path):
            return {"error": f"File not found: {adata_path}", "success": False}

        adata = sc.read_h5ad(adata_path)

        # Ensure necessary preprocessing
        if 'X_pca' not in adata.obsm:
            sc.pp.pca(adata)
        if 'neighbors' not in adata.uns:
            sc.pp.neighbors(adata)

        # Perform trajectory analysis based on method
        if method == "TrajInfer":
            traj = ov.single.TrajInfer(adata, root_cell=root_cell)
            traj.infer_trajectory()

            pseudotime = adata.obs.get('pseudotime', np.zeros(len(adata)))
            branches = len(adata.uns.get('trajectory_branches', []))
            developmental_potential = adata.obs.get('developmental_potential', np.ones(len(adata)))

        elif method == "cytotrace2":
            cytotrace_result = ov.single.cytotrace2(adata)

            pseudotime = cytotrace_result.get('pseudotime', np.zeros(len(adata)))
            branches = 1
            developmental_potential = cytotrace_result.get('cytotrace_score', np.ones(len(adata)))

        elif method == "diffmap":
            ov.single.diffmap(adata)

            if 'X_diffmap' in adata.obsm:
                pseudotime = adata.obsm['X_diffmap'][:, 0]
            else:
                pseudotime = np.zeros(len(adata))
            branches = 1
            developmental_potential = np.ones(len(adata))

        else:
            return {"error": f"Unknown method: {method}", "success": False}

        # Calculate statistics
        pseudotime_stats = {
            "min": float(np.min(pseudotime)),
            "max": float(np.max(pseudotime)),
            "mean": float(np.mean(pseudotime)),
            "std": float(np.std(pseudotime)),
        }

        dev_potential_stats = {
            "min": float(np.min(developmental_potential)),
            "max": float(np.max(developmental_potential)),
            "mean": float(np.mean(developmental_potential)),
            "std": float(np.std(developmental_potential)),
        }

        return {
            "success": True,
            "pseudotime_range": [pseudotime_stats["min"], pseudotime_stats["max"]],
            "pseudotime_statistics": pseudotime_stats,
            "trajectory_branches": branches,
            "method_used": method,
            "developmental_potential": dev_potential_stats,
            "total_cells": len(adata),
            "root_cell": root_cell,
        }

    except ImportError as e:
        return {"error": f"Missing dependency: {str(e)}", "success": False, "suggestion": "Please install omicverse: pip install omicverse"}
    except Exception as e:
        return {"error": f"Analysis failed: {str(e)}", "success": False}


get_trajectory_analysis_for_dataset_doc = {
    "name": "get_trajectory_analysis_for_dataset",
    "description": "Perform trajectory inference and pseudotime analysis on single-cell data",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string", "description": "Path to AnnData (.h5ad) file"},
            "method": {"type": "string", "enum": ["TrajInfer", "cytotrace2", "diffmap"], "description": "Trajectory inference method to use"},
            "root_cell": {"type": "string", "description": "Starting cell barcode for trajectory (optional)"},
        },
        "required": ["adata_path"],
    },
}

