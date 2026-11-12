"""Integrate reference + query with Harmony, then 15-NN vote from the reference."""
import anndata as ad
import harmonypy
import numpy as np
import scanpy as sc
from sklearn.neighbors import KNeighborsClassifier


def annotate(reference, query):
    a = ad.concat([reference, query], label="part", keys=["ref", "qry"], index_unique="-")
    sc.pp.scale(a, max_value=10)
    sc.tl.pca(a, n_comps=50, random_state=0)
    z = np.asarray(harmonypy.run_harmony(a.obsm["X_pca"], a.obs, "batch", random_state=0, verbose=False).Z_corr)
    z = z.T if z.shape[0] == 50 else z
    r = (a.obs["part"] == "ref").values
    knn = KNeighborsClassifier(n_neighbors=15).fit(z[r], reference.obs["label"].astype(str).values)
    return knn.predict(z[~r])
