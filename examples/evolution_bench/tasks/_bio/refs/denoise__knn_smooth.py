"""kNN smoothing (Wagner et al. 2017, single step): average each cell's library-normalised counts
with its 15 nearest neighbours in PCA space, then restore its library size."""
import numpy as np
import scipy.sparse as sp
from sklearn.decomposition import PCA
from sklearn.neighbors import NearestNeighbors


def denoise(adata, k=15):
    x = adata.X.toarray() if sp.issparse(adata.X) else np.asarray(adata.X, dtype=float)
    lib = x.sum(1, keepdims=True)
    n = x / lib * float(np.median(lib))
    pcs = PCA(n_components=30, random_state=0).fit_transform(np.sqrt(n))
    idx = NearestNeighbors(n_neighbors=k + 1).fit(pcs).kneighbors(pcs, return_distance=False)
    sm = n[idx].mean(1)
    return (sm / float(np.median(lib)) * lib).astype(np.float32)
