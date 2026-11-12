"""Single-cell label projection (the OpenProblems label-projection task's design): transfer cell-type
labels from labelled reference batches to unlabelled query batches of the same atlas.

`solution.py` defines `annotate(reference, query) -> labels` (one label per query cell). Both
AnnData hold log-normalised counts of 2000 highly variable genes in `X`, raw counts in
`layers["counts"]` and `obs["batch"]`; only the reference has `obs["label"]`. Query batches are
whole batches (other donors / technologies), so the transfer crosses a batch effect.

A dataset scores the mean of accuracy and macro F1 on the query; combined_score = mean over the
split's datasets (public: pancreas, lung; holdout: immune_human).
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(os.environ.get("TASK_DIR") or globals().get("__file__", ".")).resolve()
sys.path.insert(0, str(HERE.parent / "_bio"))
from biocommon import DATA, SPLIT, fail, fidelity_of, run_program  # noqa: E402

DATASETS = {"public": ["pancreas", "lung"], "holdout": ["immune_human"]}


def evaluate(workspace_path: str, fidelity: str = "full") -> dict:
    from sklearn.metrics import accuracy_score, f1_score

    t0 = time.time()
    names = DATASETS[SPLIT]
    if fidelity_of(workspace_path, fidelity) == "low":
        names = names[:1]
    out = {}
    for name in names:
        d = DATA / "labelproj"
        path, err, secs = run_program(workspace_path, "annotate",
                                      [d / f"{name}.reference.input.h5ad", d / f"{name}.query.input.h5ad"],
                                      f"{name}.json")
        if path is None:
            return fail(f"{name}: {err}", t0)
        truth = np.load(d / f"{name}.eval.npz", allow_pickle=True)["label"].astype(str)
        pred = np.asarray(json.load(open(path)), dtype=str)
        if pred.shape != truth.shape:
            return fail(f"{name}: {len(pred)} labels for {len(truth)} query cells", t0)
        acc = float(accuracy_score(truth, pred))
        f1 = float(f1_score(truth, pred, average="macro", labels=np.unique(truth), zero_division=0))
        out[name] = {"accuracy": acc, "f1_macro": f1, "score": (acc + f1) / 2, "solve_seconds": round(secs, 1)}
    combined = float(np.mean([v["score"] for v in out.values()]))
    return {"combined_score": combined, "validity": 1.0, "datasets": out,
            **{f"{k}_score": v["score"] for k, v in out.items()},
            "eval_time": time.time() - t0, "fitness_weights": {"combined_score": 1.0}}


if __name__ == "__main__":
    print(json.dumps(evaluate(sys.argv[1] if len(sys.argv) > 1 else str(HERE),
                              sys.argv[2] if len(sys.argv) > 2 else "full"), indent=1, default=str))
