# EVOLVE-BLOCK-START
"""Label projection: name the cell type of every query cell from the labelled reference.
Starting point: 15-nearest-neighbour vote in a PCA fitted on the reference."""
import numpy as np
import scipy.sparse as sp
from sklearn.decomposition import PCA
from sklearn.neighbors import KNeighborsClassifier


def annotate(reference, query):
    def dense(x):
        return x.toarray() if sp.issparse(x) else np.asarray(x)

    pca = PCA(n_components=50, random_state=0).fit(dense(reference.X))
    knn = KNeighborsClassifier(n_neighbors=15).fit(pca.transform(dense(reference.X)),
                                                   reference.obs["label"].astype(str).values)
    return knn.predict(pca.transform(dense(query.X)))
# EVOLVE-BLOCK-END
