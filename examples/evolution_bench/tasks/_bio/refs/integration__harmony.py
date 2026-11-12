"""Harmony (Korsunsky et al. 2019) on 50 PCs -- harmonypy with default settings."""
import harmonypy
import numpy as np
import scanpy as sc


def integrate(adata):
    x = adata.copy()
    sc.pp.scale(x, max_value=10)
    sc.tl.pca(x, n_comps=50, random_state=0)
    ho = harmonypy.run_harmony(x.obsm["X_pca"], x.obs, "batch", random_state=0, verbose=False)
    z = np.asarray(ho.Z_corr)
    return (z.T if z.shape[0] == 50 else z).astype(np.float32)
