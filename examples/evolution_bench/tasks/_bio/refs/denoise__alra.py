"""ALRA (Linderman et al. 2022): low-rank SVD of the log-normalised matrix, per-gene
thresholding at the magnitude of its most negative 0.1 % quantile, rescaling of the kept values
to the observed non-zeros' mean / sd, observed non-zeros restored. Re-implemented from the paper
(the reference code is R); rank chosen by the paper's spectral-gap rule."""
import numpy as np
import scipy.sparse as sp
from sklearn.utils.extmath import randomized_svd


def _choose_k(x, k_max=100, noise_start=80):
    s = randomized_svd(x, k_max, random_state=0)[1]
    d = s[:-1] - s[1:]
    noise = d[noise_start - 1:]
    thr = noise.mean() + 6 * noise.std()
    hit = np.where(d > thr)[0]
    return int(hit.max() + 1) if len(hit) else 10


def denoise(adata):
    x = adata.X.toarray() if sp.issparse(adata.X) else np.asarray(adata.X, dtype=float)
    lib = x.sum(1, keepdims=True)
    a = np.log1p(x / lib * 1e4)
    k = _choose_k(a)
    u, s, vt = randomized_svd(a, k, random_state=0)
    lr = (u * s) @ vt
    thr = np.abs(np.quantile(lr, 0.001, axis=0))
    lr[lr <= thr] = 0
    nz = a > 0
    for j in range(a.shape[1]):
        obs = a[nz[:, j], j]
        kept = lr[lr[:, j] > 0, j]
        if len(obs) > 1 and len(kept) > 1 and kept.std() > 0:
            sigma = obs.std() / kept.std()
            mu = obs.mean() - kept.mean() * sigma
            col = lr[:, j]
            col[col > 0] = col[col > 0] * sigma + mu
    lr = np.clip(lr, 0, None)
    restore = nz & (lr == 0)
    lr[restore] = a[restore]
    return (np.expm1(lr) * lib / 1e4).astype(np.float32)
