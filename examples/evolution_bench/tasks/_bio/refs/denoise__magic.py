"""MAGIC (van Dijk et al. 2018) as run in OpenProblems: library-size normalise, sqrt, MAGIC,
square, undo the normalisation."""
import magic
import numpy as np
import scipy.sparse as sp


def denoise(adata):
    x = adata.X.toarray() if sp.issparse(adata.X) else np.asarray(adata.X)
    lib = x.sum(1, keepdims=True)
    med = float(np.median(lib))
    y = np.sqrt(x / lib * med)
    z = magic.MAGIC(random_state=0, verbose=0).fit_transform(y)
    z = np.clip(np.asarray(z), 0, None) ** 2
    return (z / med * lib).astype(np.float32)
