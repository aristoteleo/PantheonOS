"""
Evaluator for Gene Panel RL Algorithm Evolution.

This evaluator measures:
1. Final panel quality (ARI, NMI, SI)
2. Panel size compliance (target ~500, max 1000)
3. Training speed
4. Convergence behavior

The combined score balances these metrics for evolution.
"""

import importlib.util
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Dict
import logging
import numpy as np

# Configuration
EVOLUTION_EPOCHS = 25  # Reduced epochs for faster evolution iterations
EVALUATION_TIMEOUT = 600  # 5 minutes max per evaluation

import logging                                                                                                         
logging.basicConfig(level=logging.INFO, force=True)                                                                      

def _get_data_dir() -> Path:
    """Get the data directory path."""
    env_data_dir = os.environ.get("GENE_PANEL_DATA_DIR")
    if env_data_dir:
        return Path(env_data_dir)
    return Path(__file__).parent / "data"


def _load_module_from_workspace(workspace_path: str, module_name: str = "rl_gene_panel"):
    """Load the RL gene panel module from workspace."""
    workspace = Path(workspace_path)
    module_path = workspace / f"{module_name}.py"

    if not module_path.exists():
        raise FileNotFoundError(f"{module_name}.py not found in {workspace_path}")

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    return module


def _load_data(data_dir: Path):
    """Load preprocessed data and scores."""
    import anndata as ad

    adata_path = data_dir / "adata_preprocessed.h5ad"
    scores_path = data_dir / "scores.pkl"

    if not adata_path.exists():
        raise FileNotFoundError(f"Preprocessed adata not found: {adata_path}")
    if not scores_path.exists():
        raise FileNotFoundError(f"Scores file not found: {scores_path}")

    adata = ad.read_h5ad(adata_path)

    with open(scores_path, "rb") as f:
        scores_data = pickle.load(f)

    return adata, scores_data


def _evaluate_panel_metrics(adata, genes: list, label_key: str = "cell_type") -> Dict[str, float]:
    """
    Compute full evaluation metrics for a gene panel.
    Uses the same metrics as evaluate_gene_panel.py.
    """
    import scanpy as sc
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
    from sklearn.metrics import pairwise_distances

    genes = [g for g in genes if g in adata.var_names]
    panel_size = len(genes)

    if panel_size < 10:
        return {
            "ari": 0.0,
            "nmi": 0.0,
            "si": 0.0,
            "panel_size": panel_size,
        }

    ad = adata[:, genes].copy()

    n_pcs = min(50, panel_size - 1, ad.n_obs - 1)
    try:
        import rapids_singlecell as rsc
        _USE_GPU = True
    except ImportError:
        _USE_GPU = False
    if _USE_GPU:
        rsc.pp.pca(ad, n_comps=n_pcs)
        rsc.pp.neighbors(ad, n_neighbors=15, use_rep="X_pca")
        rsc.tl.leiden(ad, resolution=0.8, random_state=0)
    else:
        sc.pp.pca(ad, n_comps=n_pcs)
        sc.pp.neighbors(ad, n_neighbors=15, use_rep="X_pca")
        sc.tl.leiden(ad, resolution=0.8, random_state=0)

    clusters = ad.obs["leiden"]
    true = ad.obs[label_key]

    ari = adjusted_rand_score(true, clusters)
    nmi = normalized_mutual_info_score(true, clusters)

    # Separation Index
    X = ad.obsm["X_pca"]
    labels = ad.obs["leiden"].values
    dist = pairwise_distances(X)

    intra, inter = [], []
    for g in np.unique(labels):
        idx = np.where(labels == g)[0]
        jdx = np.where(labels != g)[0]
        if len(idx) > 1:
            intra.append(dist[np.ix_(idx, idx)].mean())
        if len(jdx) > 0:
            inter.append(dist[np.ix_(idx, jdx)].mean())

    si = np.mean(inter) / np.mean(intra) if intra and inter else 0.0

    return {
        "ari": ari,
        "nmi": nmi,
        "si": si,
        "panel_size": panel_size,
    }


def _compute_size_score(panel_size: int, k_target: int = 500, k_max: int = 1000) -> float:
    """Compute panel size compliance score."""
    if panel_size <= k_target:
        return 1.0
    elif panel_size <= k_max:
        return 1.0 - (panel_size - k_target) / (k_max - k_target)
    else:
        return 0.0


def _compute_convergence_improvement(reward_history: list) -> float:
    """
    Compute convergence based on the average of the first 5 epochs and the best
    of the last 10 epochs. This assumes we will always run for at least 15 epochs.
    """
    if len(reward_history) < 15:
        return 0.0
    
    early = np.mean(reward_history[:5])
    late_best = max(reward_history[-10:])  # best in final stretch
    return max(0.0, late_best - early)


def evaluate(workspace_path: str) -> Dict[str, Any]:
    """
    Evaluate the RL gene panel implementation.

    This is the main evaluation function called by Pantheon Evolution.

    Args:
        workspace_path: Path to the workspace containing rl_gene_panel.py

    Returns:
        Dictionary with metrics and fitness_weights
    """
    workspace = Path(workspace_path)
    logging.info("=== EVALUATOR CALLED ===")

    # Load the module from workspace
    try:
        rl_module = _load_module_from_workspace(workspace_path)
    except Exception as e:
        logging.error("Failed to load rl_gene_panel.py")
        return {
            "function_score": 0.0,
            "error": f"Failed to load rl_gene_panel.py: {e}",
        }

    # Load data
    data_dir = _get_data_dir()
    try:
        adata, scores_data = _load_data(data_dir)
    except Exception as e:
        logging.error("Failed to load data")
        return {
            "function_score": 0.0,
            "error": f"Failed to load data: {e}",
        }

    # Extract prior subsets and candidate genes
    logging.info("Started to extract prior subsets...")
    meta = scores_data.get("meta_vote_result", {})
    per_method_topk = scores_data.get("per_method_topk", {})
    gtilde = list(meta.get("G_tilde", adata.var_names.tolist()[:2000]))

    # Run training with reduced epochs
    try:
        start_time = time.time()
        
        logging.info("Beginning training run...")
        result = rl_module.train_gene_panel_selector(
            adata=adata,
            gtilde=gtilde,
            prior_subsets=per_method_topk,
            label_key="cell_type",
            K_target=500,
            K_max=1000,
            epochs=EVOLUTION_EPOCHS,
            N_explore=8,
            N_optimize=5,
            verbose=True,
            alpha=0.8,
            beta=1.5,
        )

        training_time = time.time() - start_time

        best_panel = result.get("best_panel", [])
        best_reward = result.get("best_reward", 0.0)
        training_history = result.get("training_history", {})

    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Training failed: {e}",
        }

    # Verify we got a valid panel
    if not best_panel or len(best_panel) < 10:
        return {
            "function_score": 0.0,
            "error": f"Invalid panel: got {len(best_panel) if best_panel else 0} genes",
        }

    # Evaluate final panel
    try:
        panel_metrics = _evaluate_panel_metrics(adata, best_panel, label_key="cell_type")
    except Exception as e:
        return {
            "function_score": 0.1,
            "error": f"Panel evaluation failed: {e}",
            "best_reward": best_reward,
        }

    # Compute derived metrics
    final_ari = panel_metrics["ari"]
    final_nmi = panel_metrics["nmi"]
    final_si = panel_metrics["si"]
    panel_size = panel_metrics["panel_size"]

    training_speed = 1.0 / (1.0 + training_time / 60.0)

    reward_history = training_history.get("reward_history", [])
    convergence_improvement = _compute_convergence_improvement(reward_history)

    # Fitness weights for weighted scoring
    fitness_weights = {
        "final_ari": 0.35,
        "final_nmi": 0.15,
        "final_si": 0.15,
        "training_speed": 0.15,
        "convergence_improvement": 0.20,
    }

    return {
        "final_ari": final_ari,
        "final_nmi": final_nmi,
        "final_si": final_si,
        "training_speed": training_speed,
        "convergence_improvement": convergence_improvement,
        "panel_size": panel_size,
        "best_reward": best_reward,
        "training_time": training_time,
        "n_epochs": EVOLUTION_EPOCHS,
        "fitness_weights": fitness_weights,
    }


def evaluate_full(workspace_path: str, epochs: int = 50) -> Dict[str, Any]:
    """
    Full evaluation with more epochs.

    Use this after evolution is complete to validate the final selected program.

    Args:
        workspace_path: Path to the workspace containing rl_gene_panel.py
        epochs: Number of training epochs (default 50)

    Returns:
        Dictionary with full evaluation metrics
    """
    workspace = Path(workspace_path)

    try:
        rl_module = _load_module_from_workspace(workspace_path)
    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Failed to load rl_gene_panel.py: {e}",
        }

    data_dir = _get_data_dir()
    try:
        adata, scores_data = _load_data(data_dir)
    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Failed to load data: {e}",
        }

    meta = scores_data.get("meta_vote_result", {})
    per_method_topk = scores_data.get("per_method_topk", {})
    gtilde = list(meta.get("G_tilde", adata.var_names.tolist()[:2000]))

    try:
        start_time = time.time()

        result = rl_module.train_gene_panel_selector(
            adata=adata,
            gtilde=gtilde,
            prior_subsets=per_method_topk,
            label_key="cell_type",
            K_target=500,
            K_max=1000,
            epochs=epochs,
            N_explore=12,
            N_optimize=8,
            verbose=True,
            alpha=0.8,
            beta=1.5,
        )

        training_time = time.time() - start_time

        best_panel = result.get("best_panel", [])
        best_reward = result.get("best_reward", 0.0)
        training_history = result.get("training_history", {})

    except Exception as e:
        return {
            "function_score": 0.0,
            "error": f"Training failed: {e}",
        }

    if not best_panel or len(best_panel) < 10:
        return {
            "function_score": 0.0,
            "error": f"Invalid panel: got {len(best_panel) if best_panel else 0} genes",
        }

    try:
        panel_metrics = _evaluate_panel_metrics(adata, best_panel, label_key="cell_type")
    except Exception as e:
        return {
            "function_score": 0.1,
            "error": f"Panel evaluation failed: {e}",
        }

    return {
        "final_ari": panel_metrics["ari"],
        "final_nmi": panel_metrics["nmi"],
        "final_si": panel_metrics["si"],
        "panel_size": panel_metrics["panel_size"],
        "best_reward": best_reward,
        "training_time": training_time,
        "n_epochs": epochs,
        "reward_history": training_history.get("reward_history", []),
        "size_history": training_history.get("size_history", []),
    }


if __name__ == "__main__" and "__file__" in dir():
    workspace = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("Gene Panel RL Evaluator Test")
    print("=" * 60)
    print(f"Workspace: {workspace}")
    print(f"Data dir: {_get_data_dir()}")

    print("\n" + "-" * 60)
    print(f"Running evaluation (epochs={EVOLUTION_EPOCHS})...")
    print("-" * 60)

    result = evaluate(workspace)

    for key, value in sorted(result.items()):
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        elif isinstance(value, dict):
            print(f"  {key}: {value}")
        else:
            print(f"  {key}: {value}")
