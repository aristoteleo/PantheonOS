"""Logistic regression on the scaled log-normalised genes (OpenProblems' baseline classifier)."""
import numpy as np
import scipy.sparse as sp
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


def annotate(reference, query):
    d = lambda x: x.toarray() if sp.issparse(x) else np.asarray(x)  # noqa: E731
    sc = StandardScaler().fit(d(reference.X))
    clf = LogisticRegression(max_iter=1000).fit(sc.transform(d(reference.X)), reference.obs["label"].astype(str))
    return clf.predict(sc.transform(d(query.X)))
