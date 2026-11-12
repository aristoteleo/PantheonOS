"""Denoising: return the expected expression of every cell and gene. Starting point: no denoising
at all (the observed counts themselves)."""
import numpy as np
import scipy.sparse as sp


def denoise(adata):
    x = adata.X
    return (x.toarray() if sp.issparse(x) else np.asarray(x)).astype(np.float32)
