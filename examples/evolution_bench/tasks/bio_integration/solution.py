"""Batch integration: return a low-dimensional embedding of the cells in which batches mix and
cell types stay apart. Starting point: plain PCA (no batch correction at all)."""
import numpy as np
import scanpy as sc


def integrate(adata):
    x = adata.copy()
    sc.pp.scale(x, max_value=10)
    sc.tl.pca(x, n_comps=50, random_state=0)
    return np.asarray(x.obsm["X_pca"], dtype=np.float32)
