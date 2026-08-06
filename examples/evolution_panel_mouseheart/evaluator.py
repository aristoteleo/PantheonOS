"""Evaluator for DIRECT gene-panel evolution on MOUSE EMBRYONIC HEART (Pantheon Evolution).

The evolving artifact is a GENE PANEL, not code: the workspace holds ``panel.txt`` — one gene
symbol per line (mouse Title-case, e.g. Nkx2-5, Gata4), drawn from the dataset's gene universe.
Fitness is the panel's quality on panel-selection-bench (mouse embryonic heart scRNA), scored
REMOTELY on Modal (data + cached full-transcriptome ceiling live there), so this is a thin client.

quality_score = mean over dims {1 identifiability, 3 structure, 4 reconstruction, 7 prior-coverage}
of the per-dimension ``relative`` (panel score / full-transcriptome ceiling). Higher is better.
A panel is INVALID (score 0) only if it has < 2 genes in the universe.
"""
import os
import time
from typing import Any, Dict

SCORE_APP = "panelbench-evo-score-mouseheart"
SCORE_FN = "score_panel"
TARGET_SIZE = 500


def _read_panel(workspace_path: str):
    with open(os.path.join(workspace_path, "panel.txt")) as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


def _fail(reason: str, et: float) -> Dict[str, Any]:
    return {"quality_score": 0.0, "validity": 0.0, "eval_time": et, "combined_score": 0.0,
            "invalid_reason": reason, "fitness_weights": {"combined_score": 1.0}}


def evaluate(workspace_path: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        genes = _read_panel(workspace_path)
    except Exception as e:  # noqa: BLE001
        return _fail(f"cannot read panel.txt: {e}", time.time() - t0)
    if not genes:
        return _fail("panel.txt is empty", time.time() - t0)

    try:
        import modal
        fn = modal.Function.from_name(SCORE_APP, SCORE_FN)
        res = fn.remote(genes, TARGET_SIZE)
    except Exception as e:  # noqa: BLE001
        return _fail(f"remote scoring failed: {type(e).__name__}: {e}", time.time() - t0)

    et = time.time() - t0
    if not res.get("validity"):
        return _fail(res.get("invalid_reason", "invalid panel"), et)

    dims = res.get("dims", {})

    def _d(k):
        return float(dims.get(k, dims.get(str(k), 0.0)))

    q = float(res["quality"])                      # mean over dims {1,3,4,7}
    return {
        "quality_score": q,
        "quality_134": float(res.get("quality_134", 0.0)),   # dim1/3/4 sub-score (must not drop)
        "dim1_identifiability": _d(1),
        "dim3_structure": _d(3),
        "dim4_reconstruction": _d(4),
        "dim7_prior": _d(7),
        "panel_size": int(res.get("size", len(genes))),
        "validity": 1.0,
        "eval_time": et,
        "combined_score": q,                       # fitness = quality {1,3,4,7}
        "fitness_weights": {"combined_score": 1.0},
        **({"note": res["invalid_reason"]} if res.get("invalid_reason") else {}),
    }


if __name__ == "__main__" and "__file__" in globals():
    import json
    ws = os.path.dirname(os.path.abspath(__file__))
    if not os.path.exists(os.path.join(ws, "panel.txt")):
        import shutil
        shutil.copy(os.path.join(ws, "seed_panel.txt"), os.path.join(ws, "panel.txt"))
    print(json.dumps(evaluate(ws), indent=2))
