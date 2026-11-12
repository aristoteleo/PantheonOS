"""ComBat (Johnson et al. 2007) on the log-normalised genes, then PCA -- scanpy's implementation."""
import numpy as np
import scanpy as sc


def integrate(adata):
    x = adata.copy()
    x.X = x.X.toarray()
    sc.pp.combat(x, key="batch")
    x.X = np.array(x.X, dtype=np.float32)          # combat leaves a read-only array
    sc.pp.scale(x, max_value=10)
    sc.tl.pca(x, n_comps=50, random_state=0)
    return np.asarray(x.obsm["X_pca"], dtype=np.float32)
