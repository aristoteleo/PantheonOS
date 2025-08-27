"""Visualization functions using omicverse plotting capabilities"""

import os
from typing import Dict, Any, Optional, List, Union


def get_embedding_visualization_for_dataset(adata_path: str, color_by: str = "cell_type", basis: str = "umap", save_path: Optional[str] = None) -> Dict[str, Any]:
    """Generate embedding visualizations (UMAP, t-SNE, PCA) for single-cell data"""
    try:
        import scanpy as sc
        import omicverse as ov
        import matplotlib.pyplot as plt

        if not os.path.exists(adata_path):
            return {"error": f"File not found: {adata_path}", "success": False}

        adata = sc.read_h5ad(adata_path)

        basis_key = f"X_{basis}"
        if basis_key not in adata.obsm:
            if basis == "umap":
                if 'neighbors' not in adata.uns:
                    sc.pp.neighbors(adata)
                sc.tl.umap(adata)
            elif basis == "tsne":
                sc.tl.tsne(adata)
            elif basis == "pca":
                sc.pp.pca(adata)
            elif basis == "mde":
                ov.pp.mde(adata)

        fig, ax = plt.subplots(figsize=(8, 6))

        if color_by in adata.obs.columns or color_by in adata.var_names:
            if basis == "umap":
                sc.pl.umap(adata, color=color_by, ax=ax, show=False)
            elif basis == "tsne":
                sc.pl.tsne(adata, color=color_by, ax=ax, show=False)
            elif basis == "pca":
                sc.pl.pca(adata, color=color_by, ax=ax, show=False)
            else:
                sc.pl.embedding(adata, basis=basis, color=color_by, ax=ax, show=False)
        else:
            sc.pl.embedding(adata, basis=basis, color='total_counts', ax=ax, show=False)
            color_by = 'total_counts'

        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            plt.close()
        else:
            plt.close()

        unique_groups = adata.obs[color_by].nunique() if color_by in adata.obs.columns else "continuous"

        return {
            "success": True,
            "plot_type": f"{basis}_embedding",
            "colored_by": color_by,
            "total_cells": len(adata),
            "unique_groups": unique_groups,
            "saved_to": save_path if save_path else "not saved",
            "basis_coordinates": basis_key,
            "figure_size": (8, 6),
        }

    except ImportError as e:
        return {"error": f"Missing dependency: {str(e)}", "success": False, "suggestion": "Please install omicverse and matplotlib"}
    except Exception as e:
        return {"error": f"Visualization failed: {str(e)}", "success": False}


def get_gene_expression_visualization_for_dataset(adata_path: str, genes: Union[str, List[str]], plot_type: str = "violin", groupby: str = "cell_type", save_path: Optional[str] = None) -> Dict[str, Any]:
    """Visualize gene expression patterns across cell types"""
    try:
        import scanpy as sc
        import matplotlib.pyplot as plt

        if not os.path.exists(adata_path):
            return {"error": f"File not found: {adata_path}", "success": False}

        adata = sc.read_h5ad(adata_path)

        if isinstance(genes, str):
            gene_list = [g.strip() for g in genes.split(',')]
        else:
            gene_list = genes

        valid_genes = [g for g in gene_list if g in adata.var_names]
        if not valid_genes:
            return {
                "error": "No valid genes found in dataset",
                "success": False,
                "requested_genes": gene_list,
                "available_genes": list(adata.var_names[:10]),
            }

        if groupby not in adata.obs.columns:
            groupby = 'leiden' if 'leiden' in adata.obs.columns else adata.obs.columns[0]

        fig, ax = plt.subplots(figsize=(10, 6))

        if plot_type == "violin":
            sc.pl.violin(adata, keys=valid_genes, groupby=groupby, ax=ax, show=False)
        elif plot_type == "dotplot":
            sc.pl.dotplot(adata, var_names=valid_genes, groupby=groupby, ax=ax, show=False)
        elif plot_type == "heatmap":
            sc.pl.heatmap(adata, var_names=valid_genes, groupby=groupby, ax=ax, show=False)
        else:
            sc.pl.violin(adata, keys=valid_genes, groupby=groupby, ax=ax, show=False)

        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            plt.close()
        else:
            plt.close()

        return {
            "success": True,
            "plot_type": plot_type,
            "genes_plotted": valid_genes,
            "genes_not_found": [g for g in gene_list if g not in valid_genes],
            "grouped_by": groupby,
            "num_groups": adata.obs[groupby].nunique(),
            "saved_to": save_path if save_path else "not saved",
            "figure_size": (10, 6),
        }

    except ImportError as e:
        return {"error": f"Missing dependency: {str(e)}", "success": False, "suggestion": "Please install omicverse and matplotlib"}
    except Exception as e:
        return {"error": f"Visualization failed: {str(e)}", "success": False}


# Documentation
get_embedding_visualization_for_dataset_doc = {
    "name": "get_embedding_visualization_for_dataset",
    "description": "Generate embedding visualizations (UMAP, t-SNE, PCA) for single-cell data",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string", "description": "Path to AnnData file"},
            "color_by": {"type": "string", "description": "Column to color by"},
            "basis": {"type": "string", "enum": ["umap", "tsne", "pca", "mde"], "description": "Embedding type"},
            "save_path": {"type": "string", "description": "Path to save plot"},
        },
        "required": ["adata_path"],
    },
}

get_gene_expression_visualization_for_dataset_doc = {
    "name": "get_gene_expression_visualization_for_dataset",
    "description": "Visualize gene expression patterns across cell types",
    "parameters": {
        "type": "object",
        "properties": {
            "adata_path": {"type": "string", "description": "Path to AnnData file"},
            "genes": {"type": "string", "description": "Genes to plot (comma-separated)"},
            "plot_type": {"type": "string", "enum": ["violin", "dotplot", "heatmap", "boxplot"], "description": "Plot type"},
            "groupby": {"type": "string", "description": "Column to group by"},
            "save_path": {"type": "string", "description": "Path to save plot"},
        },
        "required": ["adata_path", "genes"],
    },
}

