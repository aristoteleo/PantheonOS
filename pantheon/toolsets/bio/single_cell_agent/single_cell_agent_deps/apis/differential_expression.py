"""Differential expression analysis using omicverse"""

import os
from typing import Dict, Any, Optional, List


def get_differential_expression_for_groups(adata_path: str, group_key: str, groups: Optional[List[str]] = None, method: str = "DEG") -> Dict[str, Any]:
    """Perform differential expression analysis between cell groups"""
    try:
        import scanpy as sc
        import omicverse as ov
        import pandas as pd
        import numpy as np

        if not os.path.exists(adata_path):
            return {"error": f"File not found: {adata_path}", "success": False}

        adata = sc.read_h5ad(adata_path)

        if group_key not in adata.obs.columns:
            return {"error": f"Group key '{group_key}' not found in data", "success": False, "available_keys": list(adata.obs.columns)}

        if groups is None:
            groups = adata.obs[group_key].unique().tolist()

        if method == "DEG":
            sc.tl.rank_genes_groups(adata, group_key, groups=groups, method='wilcoxon')

            de_results = []
            for group in groups[:5]:
                result = sc.get.rank_genes_groups_df(adata, group=group).head(20)
                de_results.append(result)

            if de_results:
                combined_results = pd.concat(de_results)
                significant_genes = len(combined_results[combined_results['pvals_adj'] < 0.05])
                top_genes = combined_results.head(10)['names'].tolist()
            else:
                significant_genes = 0
                top_genes = []

        elif method == "DCT":
            dct_result = ov.single.DCT(adata, group_key=group_key)

            if isinstance(dct_result, pd.DataFrame):
                significant_genes = len(dct_result[dct_result['adjusted_pvalue'] < 0.05])
                top_genes = dct_result.head(10)['gene_name'].tolist() if 'gene_name' in dct_result.columns else []
            else:
                significant_genes = 0
                top_genes = []

        elif method == "cosg":
            ov.single.cosg(adata, key_added='cosg', groupby=group_key)

            if 'cosg' in adata.uns:
                top_genes = []
                for group in groups[:5]:
                    if group in adata.uns['cosg']['names']:
                        group_genes = adata.uns['cosg']['names'][group][:5]
                        top_genes.extend(group_genes)
                significant_genes = len(top_genes)
            else:
                significant_genes = 0
                top_genes = []
        else:
            return {"error": f"Unknown method: {method}", "success": False}

        total_genes = adata.n_vars

        return {
            "success": True,
            "total_genes": total_genes,
            "total_de_genes": len(top_genes),
            "significant_genes": significant_genes,
            "top_genes": top_genes[:10] if top_genes else [],
            "method_used": method,
            "groups_compared": groups[:10] if groups else [],
            "group_key": group_key,
            "num_groups": len(groups) if groups else 0,
        }

    except ImportError as e:
        return {"error": f"Missing dependency: {str(e)}", "success": False, "suggestion": "Please install omicverse: pip install omicverse"}
    except Exception as e:
        return {"error": f"Analysis failed: {str(e)}", "success": False}


get_differential_expression_for_groups_doc = {
    "name": "get_differential_expression_for_groups",
    "description": "Perform differential expression analysis between cell groups",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string", "description": "Path to AnnData (.h5ad) file"},
            "group_key": {"type": "string", "description": "Column name for grouping (e.g., 'cell_type', 'leiden')"},
            "groups": {"type": "array", "items": {"type": "string"}, "description": "Specific groups to compare (optional, uses all if not provided)"},
            "method": {"type": "string", "enum": ["DEG", "DCT", "cosg"], "description": "Differential expression method"},
        },
        "required": ["adata_path", "group_key"],
    },
}

