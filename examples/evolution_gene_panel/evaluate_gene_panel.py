import numpy as np
import pandas as pd
import scanpy as sc
import matplotlib.pyplot as plt
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
from sklearn.metrics import pairwise_distances


# ============================================================
#                        SI METRIC
# ============================================================

def separation_index(adata, label_key="leiden"):
    """
    SI = mean(inter-cluster distances) / mean(intra-cluster distances)
    """
    X = adata.obsm["X_pca"]
    labels = adata.obs[label_key].values
    dist = pairwise_distances(X)

    intra, inter = [], []
    for g in np.unique(labels):
        idx = np.where(labels == g)[0]
        jdx = np.where(labels != g)[0]

        if len(idx) > 1:
            intra.append(dist[np.ix_(idx, idx)].mean())
        inter.append(dist[np.ix_(idx, jdx)].mean())
    return np.mean(inter) / np.mean(intra)


# ============================================================
#             FULL PANEL EVALUATION FOR ONE DATASET
# ============================================================

def evaluate_panel_full(adata, genes, label_key="cell_type", resolution=0.8, n_neighbors=15, n_pcs=50):
    """Compute NMI, ARI, SI, and panel size."""
    genes = [g for g in genes if g in adata.var_names]
    panel_size = len(genes)

    ad = adata[:, genes].copy()
    sc.pp.pca(ad, n_comps=n_pcs)
    sc.pp.neighbors(ad, n_neighbors=n_neighbors, use_rep="X_pca")
    sc.tl.leiden(ad, resolution=resolution, seed=0)

    clusters = ad.obs["leiden"]
    true = ad.obs[label_key]

    return dict(
        SIZE=panel_size,
        NMI=normalized_mutual_info_score(true, clusters),
        ARI=adjusted_rand_score(true, clusters),
        SI=separation_index(ad)
    )