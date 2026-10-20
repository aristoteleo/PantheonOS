"""
Code metrics computation utilities for evolution.

Provides metrics for MAP-Elites feature dimensions and fitness calculation.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional, Tuple


def compute_complexity(code: str, language: str = "python") -> float:
    """
    Compute code complexity metric (0.0 to 1.0).

    For Python, uses cyclomatic complexity approximation.
    For other languages, uses heuristics based on control flow keywords.

    Args:
        code: Source code string
        language: Programming language

    Returns:
        Normalized complexity score (0.0 = simple, 1.0 = complex)
    """
    if language == "python":
        return _compute_python_complexity(code)
    else:
        return _compute_generic_complexity(code)


def _compute_python_complexity(code: str) -> float:
    """Compute complexity for Python code using AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Fall back to generic method if code doesn't parse
        return _compute_generic_complexity(code)

    complexity = 1  # Base complexity

    for node in ast.walk(tree):
        # Control flow adds complexity
        if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, (ast.And, ast.Or)):
            complexity += 1
        elif isinstance(node, ast.comprehension):
            complexity += 1
        elif isinstance(node, ast.Lambda):
            complexity += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity += 1
        elif isinstance(node, ast.ClassDef):
            complexity += 2

    # Normalize to 0-1 range (assume max complexity around 100)
    return min(complexity / 100.0, 1.0)


def _compute_generic_complexity(code: str) -> float:
    """Compute complexity using keyword counting heuristics."""
    # Control flow keywords that increase complexity
    keywords = [
        r"\bif\b",
        r"\belse\b",
        r"\belif\b",
        r"\bfor\b",
        r"\bwhile\b",
        r"\btry\b",
        r"\bexcept\b",
        r"\bcatch\b",
        r"\bcase\b",
        r"\bswitch\b",
        r"\b&&\b",
        r"\|\|",
        r"\band\b",
        r"\bor\b",
    ]

    complexity = 1
    for pattern in keywords:
        complexity += len(re.findall(pattern, code))

    # Also count function definitions
    complexity += len(re.findall(r"\bdef\b|\bfunction\b|\bfunc\b", code))

    return min(complexity / 100.0, 1.0)


def compute_diversity(
    code: str,
    reference_codes: List[str],
    language: str = "python",
) -> float:
    """
    Compute diversity score compared to reference codes.

    Uses structural and lexical similarity measures.

    Args:
        code: Source code to evaluate
        reference_codes: List of reference codes to compare against
        language: Programming language

    Returns:
        Diversity score (0.0 = identical to references, 1.0 = very different)
    """
    if not reference_codes:
        return 0.5  # Neutral if no references

    similarities = []
    code_tokens = _tokenize_code(code)

    for ref_code in reference_codes:
        ref_tokens = _tokenize_code(ref_code)
        sim = _jaccard_similarity(code_tokens, ref_tokens)
        similarities.append(sim)

    # Average similarity, convert to diversity
    avg_similarity = sum(similarities) / len(similarities)
    return 1.0 - avg_similarity


def _tokenize_code(code: str) -> set:
    """Simple tokenization for similarity comparison."""
    # Split on whitespace and punctuation
    tokens = re.findall(r"\w+", code.lower())
    return set(tokens)


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0

    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union


def compute_lines_of_code(code: str) -> int:
    """Count non-empty, non-comment lines of code."""
    lines = code.split("\n")
    count = 0
    in_multiline_comment = False

    for line in lines:
        stripped = line.strip()

        # Handle multiline strings/comments
        if '"""' in stripped or "'''" in stripped:
            quote = '"""' if '"""' in stripped else "'''"
            count_in_line = stripped.count(quote)
            if count_in_line == 1:
                in_multiline_comment = not in_multiline_comment
            elif count_in_line >= 2:
                # Both open and close on same line
                pass
            continue

        if in_multiline_comment:
            continue

        # Skip empty lines and single-line comments
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        count += 1

    return count


def compute_function_count(code: str, language: str = "python") -> int:
    """Count number of function/method definitions."""
    if language == "python":
        try:
            tree = ast.parse(code)
            count = 0
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    count += 1
            return count
        except SyntaxError:
            pass

    # Fallback: regex counting
    patterns = [
        r"\bdef\s+\w+",  # Python
        r"\bfunction\s+\w+",  # JavaScript
        r"\bfunc\s+\w+",  # Go
        r"\bfn\s+\w+",  # Rust
    ]

    count = 0
    for pattern in patterns:
        count += len(re.findall(pattern, code))
    return count


def compute_class_count(code: str, language: str = "python") -> int:
    """Count number of class definitions."""
    if language == "python":
        try:
            tree = ast.parse(code)
            count = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    count += 1
            return count
        except SyntaxError:
            pass

    # Fallback: regex counting
    return len(re.findall(r"\bclass\s+\w+", code))


def compute_features(
    code: str,
    feature_dimensions: List[str],
    reference_codes: Optional[List[str]] = None,
    language: str = "python",
) -> Dict[str, float]:
    """
    Compute multiple feature dimensions for MAP-Elites.

    Args:
        code: Source code to evaluate
        feature_dimensions: List of feature names to compute
        reference_codes: Reference codes for diversity calculation
        language: Programming language

    Returns:
        Dict mapping feature names to values (0.0 to 1.0)
    """
    features: Dict[str, float] = {}
    reference_codes = reference_codes or []

    for dim in feature_dimensions:
        if dim == "complexity":
            features[dim] = compute_complexity(code, language)
        elif dim == "diversity":
            features[dim] = compute_diversity(code, reference_codes, language)
        elif dim == "size":
            loc = compute_lines_of_code(code)
            features[dim] = min(loc / 500.0, 1.0)  # Normalize to 500 LOC max
        elif dim == "function_count":
            count = compute_function_count(code, language)
            features[dim] = min(count / 20.0, 1.0)  # Normalize to 20 functions max
        elif dim == "class_count":
            count = compute_class_count(code, language)
            features[dim] = min(count / 10.0, 1.0)  # Normalize to 10 classes max
        else:
            # Unknown dimension, default to 0.5
            features[dim] = 0.5

    return features


def compute_function_score(
    metrics: Dict[str, float],
    fitness_weights: Optional[Dict[str, float]],
    metric_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
) -> float:
    """
    Compute function_score from normalized metrics with weights.

    Args:
        metrics: Dict of metric name -> value
        fitness_weights: Weight for each metric (from evaluator)
        metric_ranges: Dict of metric name -> (min, max) for normalization

    Returns:
        Normalized weighted function score (0.0 to 1.0)
    """
    if not metrics or not fitness_weights:
        return 0.0

    total_weight = 0.0
    weighted_sum = 0.0

    for metric_name, weight in fitness_weights.items():
        if metric_name not in metrics:
            continue
        value = metrics[metric_name]
        if not isinstance(value, (int, float)):
            continue

        # Normalize using observed range
        if metric_ranges and metric_name in metric_ranges:
            min_val, max_val = metric_ranges[metric_name]
            range_size = max_val - min_val
            if range_size > 1e-8:
                normalized = (float(value) - min_val) / range_size
            else:
                normalized = 0.5  # All values are the same
        else:
            # Assume [0, 1] range for metrics without range info
            normalized = max(0.0, min(1.0, float(value)))

        weighted_sum += weight * normalized
        total_weight += weight

    if total_weight < 1e-8:
        return 0.0

    return weighted_sum / total_weight


def compute_fitness_score(
    metrics: Dict[str, float],
    feature_dimensions: List[str],
    metric_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
    function_weight: float = 1.0,
    llm_weight: float = 0.0,
) -> float:
    """
    Compute overall fitness score from metrics.

    fitness = function_score × function_weight + llm_score × llm_weight

    Args:
        metrics: Dict of metric name -> value (may include fitness_weights dict)
        feature_dimensions: List of feature dimension names to exclude
        metric_ranges: Optional dict of metric name -> (min, max) for normalization
        function_weight: Weight for function_score (default 1.0)
        llm_weight: Weight for llm_score (default 0.0)

    Returns:
        Fitness score (higher is better, 0.0 to 1.0 if normalized)
    """
    if not metrics:
        return 0.0

    # Get fitness_weights for function_score calculation
    fitness_weights = metrics.get("fitness_weights")

    # Compute function_score (normalized weighted score from evaluator metrics)
    if fitness_weights and isinstance(fitness_weights, dict):
        function_score = compute_function_score(metrics, fitness_weights, metric_ranges)
    else:
        # No fitness_weights means evaluation failed or incomplete
        # Return 0.0 instead of using fallback logic
        function_score = 0.0

    # Get llm_score (already normalized to 0-1)
    llm_score = metrics.get("llm_score", 0.5)

    # Compute final fitness
    # fitness = function_score × function_weight + llm_score × llm_weight
    total_weight = function_weight + llm_weight
    if total_weight < 1e-8:
        return function_score

    fitness = (function_score * function_weight + llm_score * llm_weight) / total_weight
    return fitness


def feature_coordinates_to_bin(
    coordinates: Dict[str, float],
    feature_dimensions: List[str],
    num_bins: int = 10,
    feature_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Tuple[int, ...]:
    """
    Convert feature coordinates to bin indices for MAP-Elites grid.

    Args:
        coordinates: Dict of feature name -> value (0.0 to 1.0)
        feature_dimensions: Ordered list of feature dimension names
        num_bins: Number of bins per dimension
        feature_ranges: Optional dict of feature name -> (min, max) for adaptive ranges

    Returns:
        Tuple of bin indices
    """
    bins = []
    for dim in feature_dimensions:
        value = coordinates.get(dim, 0.5)

        # Get range for this dimension
        if feature_ranges and dim in feature_ranges:
            min_val, max_val = feature_ranges[dim]
        else:
            min_val, max_val = 0.0, 1.0

        # Normalize to [0, 1] within the range
        range_size = max_val - min_val
        if range_size > 0:
            normalized = (value - min_val) / range_size
        else:
            normalized = 0.5

        # Clamp and convert to bin index
        normalized = max(0.0, min(1.0, normalized))
        bin_idx = int(normalized * (num_bins - 1))
        bins.append(bin_idx)
    return tuple(bins)
