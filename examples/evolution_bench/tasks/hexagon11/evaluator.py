# ===--------------------------------------------------------------------------------------===#
#
# Part of the CodeEvolve Project, under the Apache License v2.0.
# See https://github.com/inter-co/science-codeevolve/blob/main/LICENSE for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for problem of packing unit regular hexagons inside
# a regular hexagon, with 11 unit hexagons.
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
import time
import json

import numpy as np
import math

N_HEX = 11
BENCHMARK = 1 / 3.930092


def hexagon_vertices(
    center_x: float,
    center_y: float,
    side_length: float,
    angle_degrees: float,
) -> list[tuple[float, float]]:
    """Generates the vertices of a regular hexagon.
    Args:
    center_x: x-coordinate of the center.
    center_y: y-coordinate of the center.
    side_length: Length of each side.
    angle_degrees: Rotation angle in degrees (clockwise from horizontal).
    Returns:
    A list of tuples, where each tuple (x, y) represents the vertex location.
    """
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices


def normalize_vector(v: tuple[float, float]) -> tuple[float, float]:
    """Normalizes a 2D vector."""
    magnitude = math.sqrt(v[0] ** 2 + v[1] ** 2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude != 0 else (0.0, 0.0)


def get_normals(vertices: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Gets the outward normals of a polygon's edges."""
    normals = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]  # Wrap around to the first vertex.
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))  # Rotate edge by 90 degrees.
        normals.append(normal)
    return normals


def project_polygon(
    vertices: list[tuple[float, float]],
    axis: tuple[float, float],
) -> tuple[float, float]:
    """Projects a polygon onto an axis and returns the min/max values."""
    min_proj = float("inf")
    max_proj = float("-inf")
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]  # Dot product.
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj


def overlap_1d(min1: float, max1: float, min2: float, max2: float, tol: float = 1e-6) -> bool:
    """Determines whether two 1D intervals overlap, allowing for numerical tolerance."""
    return max1 >= min2 - tol and max2 >= min1 - tol


def polygons_intersect(
    vertices1: list[tuple[float, float]],
    vertices2: list[tuple[float, float]],
    tol: float = 1e-6,
) -> bool:
    """Determines if two polygons intersect using the Separating Axis Theorem."""
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2
    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2, tol):
            return False  # Separating axis found, polygons are disjoint.
    return True  # No separating axis found, polygons intersect.


def hexagons_are_disjoint(
    hex1_params: tuple[float, float, float, float],
    hex2_params: tuple[float, float, float, float],
    tol: float = 1e-6,
) -> bool:
    """Determines if two hexagons are disjoint given their parameters."""
    hex1_vertices = hexagon_vertices(*hex1_params)
    hex2_vertices = hexagon_vertices(*hex2_params)
    return not polygons_intersect(hex1_vertices, hex2_vertices, tol)


def is_inside_hexagon(
    point: tuple[float, float],
    hex_params: tuple[float, float, float, float],
    tol: float = 1e-6,
) -> bool:
    """Checks if a point is inside a hexagon (given its parameters)."""
    hex_vertices = hexagon_vertices(*hex_params)
    for i in range(len(hex_vertices)):
        p1 = hex_vertices[i]
        p2 = hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0]
        if cross_product < -tol:  # Allow small numerical errors
            return False
    return True


def all_hexagons_contained(
    inner_hex_params_list: list[tuple[float, float, float, float]],
    outer_hex_params: tuple[float, float, float, float],
    tol: float = 1e-6,
) -> bool:
    """Checks if all inner hexagons are contained within the outer hexagon."""
    for inner_hex_params in inner_hex_params_list:
        inner_hex_vertices = hexagon_vertices(*inner_hex_params)
        for vertex in inner_hex_vertices:
            if not is_inside_hexagon(vertex, outer_hex_params, tol):
                return False
    return True


def verify_construction(
    inner_hex_data: tuple[float, float, float],
    outer_hex_center: tuple[float, float],
    outer_hex_side_length: float,
    outer_hex_angle_degrees: float,
    tol: float = 1e-6,
):
    """Verifies the hexagon packing construction with a rotated outer hexagon.
    Args:
    inner_hex_data: List of (x, y, angle_degrees) tuples for inner hexagons.
    outer_hex_center: (x, y) tuple for the outer hexagon center.
    outer_hex_side_length: Side length of the outer hexagon.
    outer_hex_angle_degrees: Rotation angle of the outer hexagon in degrees.
    tol: Numerical tolerance for geometric checks (default: 1e-6).
    Raises:
    AssertionError if the construction is not valid.
    """
    inner_hex_params_list = [
        (x, y, 1, angle) for x, y, angle in inner_hex_data
    ]  # Sets the side length to 1.
    outer_hex_params = (
        outer_hex_center[0],
        outer_hex_center[1],
        outer_hex_side_length,
        outer_hex_angle_degrees,
    )
    # Disjointness check.
    for i in range(len(inner_hex_params_list)):
        for j in range(i + 1, len(inner_hex_params_list)):
            if not hexagons_are_disjoint(
                inner_hex_params_list[i], inner_hex_params_list[j], tol
            ):
                raise AssertionError(f"Hexagons {i+1} and {j+1} intersect!")
    # Containment check.
    if not all_hexagons_contained(inner_hex_params_list, outer_hex_params, tol):
        raise AssertionError("Not all inner hexagons are contained in the outer hexagon!")
    print("Construction is valid.")


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
        out = _load_solution(workspace_path).hexagon_packing_11()

        inner, outer, side = out
        inner = np.asarray(inner, dtype=float); outer = np.asarray(outer, dtype=float)
        if not (side > 0):
            raise ValueError("outer side length must be positive")
        if np.isnan(inner).any() or np.isnan(outer).any():
            raise ValueError("nan entry")
        if inner.shape != (N_HEX, 3) or outer.shape != (3,):
            raise ValueError(f"expected shapes {(N_HEX, 3)} and (3,), got {inner.shape} and {outer.shape}")
        verify_construction(inner, outer[:2], side, outer[-1])
        metric = 1.0 / float(side); extra = {"outer_side": float(side)}
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}", t0)
    ratio = float(metric / BENCHMARK)
    return {"inv_outer_hex_side_length": float(metric), "benchmark_ratio": ratio, "validity": 1.0,
            "combined_score": ratio, "eval_time": _time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}, **extra}
