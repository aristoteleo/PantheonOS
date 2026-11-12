"""Scanorama (Hie et al. 2019) embedding (100 dims) -- the scanorama package with default settings."""
import numpy as np
import scanorama
import scipy.sparse as sp


def integrate(adata):
    b = adata.obs["batch"].astype(str).values
    order = sorted(set(b))
    idx = [np.where(b == k)[0] for k in order]
    mats = [sp.csr_matrix(adata.X[i]) for i in idx]
    genes = [list(adata.var_names)] * len(mats)
    integrated, _ = scanorama.integrate(mats, genes, dimred=100, seed=0, verbose=0)
    out = np.zeros((adata.n_obs, integrated[0].shape[1]), dtype=np.float32)
    for i, z in zip(idx, integrated):
        out[i] = z
    return out
