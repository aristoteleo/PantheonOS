# ===--------------------------------------------------------------------------------------===#
#
# Part of the CodeEvolve Project, under the Apache License v2.0.
# See https://github.com/inter-co/science-codeevolve/blob/main/LICENSE for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for problem of minimizing the ratio of maximum
# to minimum distance on dimension 2 and with 16 points.
#
# ===--------------------------------------------------------------------------------------===#
#
# Some of the code in this file is adapted from:
#
# https://github.com/google-deepmind/alphaevolve_results:
# Licensed under the Apache License v2.0.
#
# ===--------------------------------------------------------------------------------------===#

import sys
import os
from importlib import __import__
import scipy as sp
import time
import numpy as np
import json

NUM_POINTS = 16
DIMENSION = 2
BENCHMARK = 1 / 12.889266112


# ---- bench-task edition ---------------------------------------------------------------------
# The verification helpers above are CodeEvolve's (Apache-2.0), themselves adapted from
# google-deepmind/alphaevolve_results; the checks and metrics below are the same ones their
# `evaluate(program_path, results_path)` computes, returned in the bench's contract. Fitness is
# CodeEvolve's `benchmark_ratio` (1.0 = AlphaEvolve's reported result), so scores compare across
# tasks and to their tables. No module-scope `__file__`: CodeEvaluator exec()s this source.
import importlib.util
import time as _time


def _load_solution(workspace_path):
    spec = importlib.util.spec_from_file_location("solution", os.path.join(workspace_path, "solution.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fail(reason, t0, **extra):
    return {"validity": 0.0, "combined_score": 0.0, "benchmark_ratio": 0.0, "invalid_reason": str(reason)[:300],
            "eval_time": _time.time() - t0, "fitness_weights": {"combined_score": 1.0}, **extra}


def evaluate(workspace_path, fidelity="full"):
    t0 = _time.time()
    try:
        out = _load_solution(workspace_path).min_max_dist_dim2_16()

        points = np.asarray(out, dtype=float)
        if points.shape != (NUM_POINTS, DIMENSION):
            raise ValueError(f"expected shape {(NUM_POINTS, DIMENSION)}, got {points.shape}")
        d = sp.spatial.distance.pdist(points)
        metric = (float(np.min(d)) / float(np.max(d))) ** 2 if np.max(d) > 0 else 0.0; extra = {}
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}", t0)
    ratio = float(metric / BENCHMARK)
    return {"min_max_ratio": float(metric), "benchmark_ratio": ratio, "validity": 1.0,
            "combined_score": ratio, "eval_time": _time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}, **extra}
