"""
Evaluator for Harmony Algorithm Evolution.

This evaluator measures:
1. Integration quality (how well batches are mixed)
2. Biological variance preservation (how well structure is preserved)
3. Execution speed
4. Convergence behavior

The combined score balances these metrics for evolution.
"""

import os
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'

import numpy as np
import pandas as pd
import time
import sys
import importlib.util

# Force CPU to avoid MPS crashes in subprocess evaluation
try:
    import torch
    torch.backends.mps.is_available = lambda: False
except ImportError:
    pass
from pathlib import Path
from sklearn.cluster import KMeans
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import silhouette_score
from typing import Dict, Any, Tuple, Optional


# =============================================================================
# Shared metrics functions (inlined for isolated workspace execution)
# =============================================================================

def load_tma_data(
    data_dir: Path,
    split: str = "train",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load TMA data with real cell type labels."""
    df = pd.read_csv(data_dir / f"tma_8000_{split}.csv")
    X = df.iloc[:, :30].values  # PC1-PC30
    batch_labels = df["donor"].values
    celltype_labels = df["celltype"].values
    return X, batch_labels, celltype_labels


def compute_batch_mixing_score(
    X: np.ndarray,
    batch_labels: np.ndarray,
    k: int = 50,
) -> float:
    """Compute batch mixing score using k-nearest neighbors."""
    n_cells = X.shape[0]
    unique_batches = np.unique(batch_labels)

    expected_props = np.array([
        np.sum(batch_labels == b) / n_cells
        for b in unique_batches
    ])

    nn = NearestNeighbors(n_neighbors=min(k + 1, n_cells), algorithm="auto")
    nn.fit(X)
    _, indices = nn.kneighbors(X)

    mixing_scores = []
    for i in range(n_cells):
        neighbor_batches = batch_labels[indices[i, 1:]]
        observed_props = np.array([
            np.sum(neighbor_batches == b) / k
            for b in unique_batches
        ])
        score = 1 - np.sqrt(np.mean((observed_props - expected_props) ** 2))
        mixing_scores.append(max(0, score))

    return np.mean(mixing_scores)


def compute_bio_conservation_score(
    X_corrected: np.ndarray,
    X_original: np.ndarray,
    true_labels: np.ndarray,
) -> float:
    """Compute biological structure conservation score using silhouette score."""
    try:
        if len(np.unique(true_labels)) > 1:
            silhouette = silhouette_score(X_corrected, true_labels)
            return (silhouette + 1) / 2
        else:
            return 0.5
    except Exception:
        return 0.5


def _get_data_dir() -> Path:
    """Get the shared data directory path."""
    # First check environment variable
    env_data_dir = os.environ.get("HARMONY_DATA_DIR")
    if env_data_dir:
        return Path(env_data_dir)

    # Default to shared data directory
    return Path(__file__).parent.parent / "data"


def generate_test_data(
    n_cells: int = 2000,
    n_features: int = 50,
    n_batches: int = 3,
    n_clusters: int = 5,
    batch_effect_strength: float = 2.0,
    random_state: int = 42,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Generate synthetic single-cell data with batch effects.

    Args:
        n_cells: Total number of cells
        n_features: Number of features (PCs)
        n_batches: Number of batches
        n_clusters: Number of biological clusters
        batch_effect_strength: Strength of batch effects
        random_state: Random seed

    Returns:
        X: Data matrix (n_cells x n_features)
        batch_labels: Batch assignments
        true_labels: True biological cluster labels
    """
    np.random.seed(random_state)

    cells_per_batch = n_cells // n_batches

    X_list = []
    batch_list = []
    label_list = []

    # Generate cluster centers
    cluster_centers = np.random.randn(n_clusters, n_features) * 3

    for batch_idx in range(n_batches):
        # Batch-specific offset
        batch_offset = np.random.randn(n_features) * batch_effect_strength

        for cluster_idx in range(n_clusters):
            # Cells per cluster per batch
            n = cells_per_batch // n_clusters

            # Generate cells around cluster center with batch effect
            cells = (
                cluster_centers[cluster_idx]
                + np.random.randn(n, n_features) * 0.5
                + batch_offset
            )

            X_list.append(cells)
            batch_list.extend([batch_idx] * n)
            label_list.extend([cluster_idx] * n)

    X = np.vstack(X_list)
    batch_labels = np.array(batch_list)
    true_labels = np.array(label_list)

    return X, batch_labels, true_labels


def load_pbmc_data(
    data_dir: Optional[Path] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Load real PBMC data from CSV files.

    Args:
        data_dir: Directory containing the data files (defaults to shared data dir)

    Returns:
        X_train: Training features (n_train x 30)
        batch_train: Training batch labels
        X_val: Validation features (n_val x 30)
        batch_val: Validation batch labels
    """
    if data_dir is None:
        data_dir = _get_data_dir()

    # Read train data
    train_df = pd.read_csv(data_dir / "pbmc_3500_full_train.csv")
    X_train = train_df.iloc[:, :30].values  # PC1-PC30
    batch_train = train_df["donor"].values

    # Read val data
    val_df = pd.read_csv(data_dir / "pbmc_3500_full_val.csv")
    X_val = val_df.iloc[:, :30].values
    batch_val = val_df["donor"].values

    return X_train, batch_train, X_val, batch_val


def generate_pseudo_labels(X: np.ndarray, n_clusters: int = 5) -> np.ndarray:
    """
    Generate pseudo cell type labels using K-means clustering.

    Clusters the original (uncorrected) data to create reference labels
    for evaluating biological structure preservation.

    Args:
        X: Data matrix (n_cells x n_features)
        n_clusters: Number of clusters

    Returns:
        Cluster labels for each cell
    """
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    return kmeans.fit_predict(X)


def compute_convergence_score(objectives: list) -> float:
    """
    Compute convergence behavior score.

    Rewards fast and stable convergence.

    Args:
        objectives: List of objective values over iterations

    Returns:
        Convergence score in [0, 1]
    """
    if len(objectives) < 2:
        return 0.5

    # Check if converged (objective stabilized)
    final_change = abs(objectives[-1] - objectives[-2]) / (abs(objectives[-2]) + 1e-8)

    # Reward small final change
    convergence_quality = np.exp(-final_change * 10)

    # Reward fewer iterations (faster convergence)
    speed_bonus = 1.0 / (1 + len(objectives) / 10)

    return 0.7 * convergence_quality + 0.3 * speed_bonus


def evaluate(workspace_path: str) -> Dict[str, Any]:
    """
    Evaluate the Harmony implementation on TMA training data.

    This is the main evaluation function called by Pantheon Evolution.
    Uses real cell type labels for accurate biological conservation scoring.

    Args:
        workspace_path: Path to the workspace containing harmony.py

    Returns:
        Dictionary with metrics and fitness_weights
    """
    workspace = Path(workspace_path)

    # Load the harmony module from workspace
    harmony_path = workspace / "harmony.py"
    if not harmony_path.exists():
        return {
            "function_score": 0.0,
            "error": "harmony.py not found",
        }

    try:
        spec = importlib.util.spec_from_file_location("harmony", harmony_path)
        harmony_module = importlib.util.module_from_spec(spec)
        sys.modules["harmony"] = harmony_module
        spec.loader.exec_module(harmony_module)
    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Failed to load harmony.py: {e}",
        }

    # Load TMA training data with real cell type labels
    data_dir = _get_data_dir()

    try:
        X_train, batch_train, celltype_train = load_tma_data(data_dir, split="train")
    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Failed to load TMA data: {e}",
        }

    n_cells = len(X_train)

    # Run harmony on training data and measure time
    try:
        # Convert batch labels to DataFrame for official harmonypy API
        meta_data = pd.DataFrame({"batch": batch_train})

        start_time = time.time()
        hm = harmony_module.run_harmony(
            X_train,
            meta_data,
            vars_use="batch",
            nclust=50,
            max_iter_harmony=10,
            random_state=42,
            verbose=False,
        )
        execution_time = time.time() - start_time

        X_corrected = hm.Z_corr
        objectives = hm.objectives

    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Harmony execution failed: {e}",
        }

    # Correctness check: verify that the algorithm actually corrected the data
    # This prevents "metric hacking" where the algorithm does nothing but scores well
    correction_magnitude = np.abs(X_train - X_corrected).mean()
    if correction_magnitude < 0.01:
        return {
            "function_score": 0.0,
            "correction_magnitude": correction_magnitude,
            "error": "No meaningful correction applied (data unchanged)",
        }

    # Compute metrics on training data using real cell type labels
    try:
        # Batch mixing (higher = better, weight: 0.4)
        mixing_score = compute_batch_mixing_score(X_corrected, batch_train)

        # Biological conservation using REAL cell type labels (weight: 0.3)
        bio_score = compute_bio_conservation_score(X_corrected, X_train, celltype_train)

        # Speed score (faster = better, weight: 0.2)
        speed_score = 1.0 / (1 + execution_time)

        # Convergence score (weight: 0.1)
        conv_score = compute_convergence_score(objectives)

        # Fitness weights for normalized scoring
        fitness_weights = {
            "mixing_score": 0.45,
            "bio_conservation_score": 0.45,
            "speed_score": 0.05,
            "convergence_score": 0.05,
        }

        return {
            "mixing_score": mixing_score,
            "bio_conservation_score": bio_score,
            "speed_score": speed_score,
            "convergence_score": conv_score,
            "fitness_weights": fitness_weights,
        }

    except Exception as e:
        return {
            "function_score": 0.1,  # Partial credit for running
            "error": f"Metric computation failed: {e}",
        }


def _evaluate_on_split(workspace_path: str, split: str) -> Dict[str, Any]:
    """
    Evaluate the Harmony implementation on a specific TMA data split.

    Args:
        workspace_path: Path to the workspace containing harmony.py
        split: Which split to evaluate on ("train", "val", or "test")

    Returns:
        Dictionary with metrics
    """
    workspace = Path(workspace_path)

    # Load the harmony module from workspace
    harmony_path = workspace / "harmony.py"
    if not harmony_path.exists():
        return {
            "function_score": 0.0,
            "error": "harmony.py not found",
        }

    try:
        spec = importlib.util.spec_from_file_location("harmony", harmony_path)
        harmony_module = importlib.util.module_from_spec(spec)
        sys.modules["harmony"] = harmony_module
        spec.loader.exec_module(harmony_module)
    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Failed to load harmony.py: {e}",
        }

    # Load TMA data
    data_dir = _get_data_dir()

    try:
        X, batch_labels, celltype_labels = load_tma_data(data_dir, split=split)
    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Failed to load TMA {split} data: {e}",
        }

    n_cells = len(X)

    # Run harmony
    try:
        # Convert batch labels to DataFrame for official harmonypy API
        meta_data = pd.DataFrame({"batch": batch_labels})

        start_time = time.time()
        hm = harmony_module.run_harmony(
            X,
            meta_data,
            vars_use="batch",
            nclust=50,
            max_iter_harmony=10,
            random_state=42,
            verbose=False,
        )
        execution_time = time.time() - start_time

        X_corrected = hm.Z_corr
        objectives = hm.objectives

    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Harmony execution failed: {e}",
        }

    # Compute metrics using real cell type labels
    try:
        mixing_score = compute_batch_mixing_score(X_corrected, batch_labels)
        bio_score = compute_bio_conservation_score(X_corrected, X, celltype_labels)
        speed_score = 1.0 / (1 + execution_time)
        conv_score = compute_convergence_score(objectives)

        # Fitness weights for normalized scoring
        fitness_weights = {
            "mixing_score": 0.45,
            "bio_conservation_score": 0.45,
            "speed_score": 0.05,
            "convergence_score": 0.05,
        }

        return {
            "mixing_score": mixing_score,
            "bio_conservation_score": bio_score,
            "speed_score": speed_score,
            "convergence_score": conv_score,
            "fitness_weights": fitness_weights,
        }

    except Exception as e:
        return {
            "function_score": 0.1,
            "error": f"Metric computation failed: {e}",
        }


def evaluate_on_validation(workspace_path: str) -> Dict[str, Any]:
    """
    Evaluate the Harmony implementation on the TMA VALIDATION set.

    This function should be used AFTER evolution is complete to evaluate
    the final selected program on held-out data.

    Args:
        workspace_path: Path to the workspace containing harmony.py

    Returns:
        Dictionary with validation metrics
    """
    return _evaluate_on_split(workspace_path, "val")


def evaluate_on_test(workspace_path: str) -> Dict[str, Any]:
    """
    Evaluate the Harmony implementation on the TMA TEST set.

    This function should be used for final evaluation after all
    hyperparameter tuning is complete.

    Args:
        workspace_path: Path to the workspace containing harmony.py

    Returns:
        Dictionary with test metrics
    """
    return _evaluate_on_split(workspace_path, "test")


# Guard against execution when code is injected into subprocess (where __file__ is not defined)
if __name__ == "__main__" and "__file__" in dir():
    # Test the evaluator locally

    # Use current directory as workspace
    workspace = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("Harmony Evaluator Test (TMA Data with Real Cell Types)")
    print("=" * 60)
    print(f"Workspace: {workspace}")

    # Training set evaluation (used during evolution)
    print("\n" + "-" * 60)
    print("TRAINING SET Evaluation (used during evolution):")
    print("-" * 60)
    result = evaluate(workspace)
    for key, value in result.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        elif isinstance(value, int):
            print(f"  {key}: {value}")
        else:
            print(f"  {key}: {value}")

    # Validation set evaluation (used after evolution)
    print("\n" + "-" * 60)
    print("VALIDATION SET Evaluation (used after evolution):")
    print("-" * 60)
    val_result = evaluate_on_validation(workspace)
    for key, value in val_result.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        elif isinstance(value, int):
            print(f"  {key}: {value}")
        else:
            print(f"  {key}: {value}")

    # Test set evaluation (final evaluation)
    print("\n" + "-" * 60)
    print("TEST SET Evaluation (final evaluation):")
    print("-" * 60)
    test_result = evaluate_on_test(workspace)
    for key, value in test_result.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        elif isinstance(value, int):
            print(f"  {key}: {value}")
        else:
            print(f"  {key}: {value}")
