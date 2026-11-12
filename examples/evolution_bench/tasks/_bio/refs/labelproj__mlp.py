"""Multi-layer perceptron (OpenProblems' MLP baseline): sklearn MLPClassifier on 100 PCs."""
import numpy as np
import scipy.sparse as sp
from sklearn.decomposition import PCA
from sklearn.neural_network import MLPClassifier


def annotate(reference, query):
    d = lambda x: x.toarray() if sp.issparse(x) else np.asarray(x)  # noqa: E731
    pca = PCA(n_components=100, random_state=0).fit(d(reference.X))
    clf = MLPClassifier(hidden_layer_sizes=(100, 100), max_iter=300, random_state=0)
    clf.fit(pca.transform(d(reference.X)), reference.obs["label"].astype(str))
    return clf.predict(pca.transform(d(query.X)))
