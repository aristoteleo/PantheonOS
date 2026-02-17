"""
Data preprocessing script for gene panel selection.
Based on the structure in final_project.ipynb.

Outputs:
- Preprocessed anndata object (.h5ad)
- Scores dictionary (.pkl)

Usage:
    python run_preprocessing.py
    python run_preprocessing.py --downsample_cell_number 5000
"""
import argparse
import os
import pickle
import warnings
import numpy as np
import pandas as pd
import scanpy as sc
import anndata as ad
from sklearn.ensemble import RandomForestClassifier
from typing import Dict, List, Tuple, Set

warnings.filterwarnings("ignore")

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DATA_PATH = os.path.expanduser("~/Downloads/134d34af-cbcd-4837-9310-3d1f83ec6f18.h5ad")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
TISSUE = "kidney"
CELLTYPE_KEY = "cell_type"
METHODS = ["hvg", "differential_expression", "random_forest", "scgenefit"]

# ==============================================================================
# GENE SCORING METHODS (without toolset wrapper)
# ==============================================================================

def score_hvg(adata, n_top_genes: int = 1000) -> pd.DataFrame:
    """Highly Variable Genes using Scanpy. Score = dispersions_norm."""
    print("[HVG] Computing highly variable genes...")
    adata_copy = adata.copy()
    sc.pp.highly_variable_genes(adata_copy, n_top_genes=n_top_genes)

    df = pd.DataFrame({
        "gene": adata_copy.var_names,
        "score": adata_copy.var["dispersions_norm"].values
    }).sort_values("score", ascending=False)

    print(f"[HVG] Scored {len(df)} genes")
    return df.reset_index(drop=True)


def score_differential_expression(adata, label_key: str = "cell_type",
                                   collapse: bool = True) -> pd.DataFrame:
    """Differential expression using Wilcoxon. Score = |log2 fold change|."""
    print(f"[DE] Computing differential expression with label_key={label_key}...")
    adata_copy = adata.copy()

    # Run DE
    sc.tl.rank_genes_groups(
        adata_copy, groupby=label_key, method="wilcoxon", reference="rest"
    )

    names = adata_copy.uns["rank_genes_groups"]["names"]
    lfc = adata_copy.uns["rank_genes_groups"]["logfoldchanges"]
    clusters = names.dtype.names

    rows = []
    for cl in clusters:
        for g, s in zip(names[cl], np.abs(lfc[cl])):
            rows.append({"gene": g, "cluster": cl, "score": float(s)})

    df = pd.DataFrame(rows)

    if collapse:
        df = df.groupby("gene", as_index=False)["score"].max()
        df = df.sort_values("score", ascending=False)

    print(f"[DE] Scored {len(df)} genes")
    return df.reset_index(drop=True)


def score_random_forest(adata, label_key: str = "cell_type",
                        random_state: int = 42) -> pd.DataFrame:
    """Random Forest feature importance."""
    print(f"[RF] Computing Random Forest importances with label_key={label_key}...")

    X = adata.X.toarray() if hasattr(adata.X, 'toarray') else np.array(adata.X)
    y = adata.obs[label_key].astype("category").cat.codes.values

    clf = RandomForestClassifier(
        n_estimators=300, random_state=random_state, n_jobs=-1
    )
    clf.fit(X, y)

    df = pd.DataFrame({"gene": adata.var_names, "score": clf.feature_importances_})
    df = df.sort_values("score", ascending=False)

    print(f"[RF] Scored {len(df)} genes")
    return df.reset_index(drop=True)


def score_scgenefit(adata, label_key: str = "cell_type",
                    n_top_genes: int = 200, method: str = "centers") -> pd.DataFrame:
    """scGeneFit LP weights. Score = LP variable x_i."""
    print(f"[scGeneFit] Computing scGeneFit scores with method={method}...")

    import scGeneFit.functions as gf

    # Access internal functions exactly as in toolset.py
    _sample        = getattr(gf, "__sample")
    _pairwise      = getattr(gf, "__select_constraints_pairwise")
    _pairwise_cent = getattr(gf, "__select_constraints_centers")
    _summarized    = getattr(gf, "__select_constraints_summarized")
    _lp            = getattr(gf, "__lp_markers")

    X = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
    y = adata.obs[label_key].astype("category").values

    samples, labs, _ = _sample(X, y, 1.0)

    if method == "pairwise_centers":
        C, nrm = _pairwise_cent(X, y, samples, labs)
    elif method == "pairwise":
        C, nrm = _pairwise(X, y, samples, labs, 3)
    else:  # "centers" or default
        C, nrm = _summarized(X, y, 0.01)

    sol = _lp(C, n_top_genes, nrm)
    w = np.asarray(sol["x"][:X.shape[1]], float)

    df = pd.DataFrame({"gene": adata.var_names, "score": w})
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    print(f"[scGeneFit] Scored {len(df)} genes")
    return df


# ==============================================================================
# SCORE PROCESSING
# ==============================================================================

def normalize_scores(scores_df: pd.DataFrame, score_cols: List[str]) -> pd.DataFrame:
    """
    Normalize gene selection scores:
    1. Handle infinite values in DE scores
    2. Rank-normalize all scores to [0, 1] percentile scale
    """
    scores_df = scores_df.copy()

    # Handle infinities in differential expression scores
    de_col = "differential_expression_score"
    if de_col in scores_df.columns:
        finite_values = scores_df.loc[np.isfinite(scores_df[de_col]), de_col]
        if len(finite_values) > 0:
            cap_value = np.percentile(finite_values, 99.9)
            scores_df[de_col] = scores_df[de_col].replace([np.inf, -np.inf], cap_value)
            print(f"[INFO] Replaced inf in '{de_col}' with cap = {cap_value:.4f}")

    # Rank normalization to percentile scale [0, 1]
    for col in score_cols:
        if col in scores_df.columns:
            scores_df[col] = scores_df[col].rank(method="average") / len(scores_df)
            print(f"[OK] Normalized '{col}' to percentile scale (0–1)")

    return scores_df


# ==============================================================================
# META-VOTE COMPONENTS (from meta_vote.py)
# ==============================================================================

def get_topk_per_method(scores_df: pd.DataFrame, methods: List[str], k: int
) -> Tuple[Dict[str, List[str]], Set[str]]:
    """Extract top-k scoring genes per selection method."""
    print(f"\n[Top-K Extraction] Selecting top {k} genes per method...")

    per_method_topk: Dict[str, List[str]] = {}
    total_genes = len(scores_df)

    for method in methods:
        col = f"{method}_score"
        if col not in scores_df.columns:
            raise KeyError(f"Missing score column '{col}' in scores_df.")
        top_genes = (
            scores_df.sort_values(by=col, ascending=False)
                     .head(k)["gene"]
                     .tolist()
        )
        per_method_topk[method] = top_genes
        print(f" - {method:25s} | top-k={len(top_genes)} genes")

    U = set().union(*per_method_topk.values()) if per_method_topk else set()
    print(f"\n[Union Summary] |Union of top-k over {len(methods)} methods| = {len(U)}")

    return per_method_topk, U


def reward_panel(adata, genes: List[str], label_key='cell_type', *,
                 n_neighbors=15, resolution=1, n_pcs=50,
                 alpha=0.8, K_target=1000, K_max=2000, beta=1.5) -> Dict:
    """Evaluate a candidate gene panel by clustering performance."""
    from sklearn.metrics import adjusted_rand_score

    genes = [g for g in genes if g in adata.var_names]
    K = len(genes)

    ad = adata[:, genes].copy()

    sc.pp.pca(ad, n_comps=min(n_pcs, K - 1, ad.n_obs - 1))
    sc.pp.neighbors(ad, n_neighbors=n_neighbors, use_rep='X_pca')
    sc.tl.leiden(ad, resolution=resolution, random_state=0)

    clusters = ad.obs['leiden']
    true = ad.obs[label_key]

    ari = adjusted_rand_score(true, clusters)

    if K <= K_target:
        size_term = 1.0
    elif K_target < K <= K_max:
        size_term = (1 - (K - K_target) / (K_max - K_target)) ** beta
    else:
        size_term = 0.0

    reward = alpha * ari + (1 - alpha) * size_term

    return dict(reward=reward, ari=ari, size_term=size_term, num_genes=K)


def compute_reliability_weights(adata, scores_df: pd.DataFrame, methods: List[str],
                                 label_key: str = 'cell_type', top_k: int = 500,
                                 **reward_kwargs) -> Tuple[Dict, Dict, Dict, Set]:
    """Compute method reliability weights via reward_panel."""
    print(f"\n[Reliability] Evaluating top-{top_k} panels for each method...")

    per_method_topk, union_set = get_topk_per_method(scores_df, methods, top_k)
    reliability = {}

    for method, genes in per_method_topk.items():
        out = reward_panel(adata, genes, label_key=label_key, **reward_kwargs)
        reliability[method] = out['reward']
        print(f" - {method:25s} | r_i = {out['reward']:.4f}")

    r_values = np.array([reliability[m] for m in methods], dtype=float)
    denom = r_values.sum()
    if denom == 0:
        weights = {m: 1.0 / len(methods) for m in methods}
    else:
        weights = {m: float(r_values[i] / denom) for i, m in enumerate(methods)}

    print("\n[Normalized Weights]")
    for method in methods:
        print(f" - {method:25s} w_i = {weights[method]:.4f}")

    return reliability, weights, per_method_topk, union_set


def meta_vote(scores_df: pd.DataFrame, methods: List[str], weights: Dict[str, float],
              per_method_topk: Dict[str, List[str]], sigma: float = 2.0) -> Dict:
    """Perform meta-vote aggregation of gene selection methods."""
    U = set().union(*per_method_topk.values())
    if len(U) == 0:
        raise ValueError("Union of top-k genes is empty")

    print(f"\n[Meta-Vote] Computing weighted meta-scores on union ({len(U)} genes)...")

    df = scores_df.loc[scores_df["gene"].isin(U)].copy()
    score_cols = [f"{m}_score" for m in methods]

    # Normalize within union
    for col in score_cols:
        mu = df[col].mean()
        std = df[col].std(ddof=0)
        df[col] = (df[col] - mu) / (std + 1e-9)

    # Weighted meta-score
    W = np.array([weights[m] for m in methods], dtype=float)
    S = df[score_cols].to_numpy()
    df["meta_score"] = S.dot(W)

    mu = df["meta_score"].mean()
    std = df["meta_score"].std(ddof=0)
    threshold = mu + sigma * std

    G_tilde = set(df.loc[df["meta_score"] > threshold, "gene"])

    print(f" - Threshold (μ + {sigma}σ) = {threshold:.4f}")
    print(f" - Retained genes = {len(G_tilde)}")

    meta_scores = (
        df[["gene", "meta_score"]]
        .sort_values("meta_score", ascending=False)
        .reset_index(drop=True)
    )

    return {
        "G_tilde": G_tilde,
        "meta_scores": meta_scores,
        "union_size": len(U),
        "threshold": threshold,
    }


# ==============================================================================
# MAIN PREPROCESSING PIPELINE
# ==============================================================================

def main(downsample_cell_number: int = None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # -------------------------------------------------------------------------
    # Step 1: Load data
    # -------------------------------------------------------------------------
    print("=" * 60)
    print("STEP 1: Loading data")
    print("=" * 60)
    adata = ad.read_h5ad(DATA_PATH)
    print(f"Loaded adata: {adata.n_obs} cells x {adata.n_vars} genes")
    print(f"Available obs keys: {list(adata.obs.keys())}")

    if 'tissue' in adata.obs.columns:
        print(f"Tissue distribution:\n{adata.obs['tissue'].value_counts()}")

    # -------------------------------------------------------------------------
    # Step 2: Subset by tissue and normalize
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print(f"STEP 2: Subsetting to tissue={TISSUE}")
    print("=" * 60)

    if 'tissue' in adata.obs.columns:
        adata = adata[adata.obs['tissue'] == TISSUE].copy()
    print(f"After tissue filter: {adata.n_obs} cells x {adata.n_vars} genes")

    # Remove genes with zero expression
    sc.pp.filter_genes(adata, min_cells=1)
    print(f"After gene filter: {adata.n_obs} cells x {adata.n_vars} genes")
    # Remove cells with zero expression
    sc.pp.filter_cells(adata, min_genes=1)
    print(f"After cell filter: {adata.n_obs} cells x {adata.n_vars} genes")

    # -------------------------------------------------------------------------
    # Step 2b: Downsample cells if requested
    # -------------------------------------------------------------------------
    if downsample_cell_number is not None and downsample_cell_number < adata.n_obs:
        print(f"\nDownsampling to {downsample_cell_number} cells...")
        sc.pp.subsample(adata, n_obs=downsample_cell_number, random_state=42)
        print(f"After downsampling: {adata.n_obs} cells x {adata.n_vars} genes")
        sc.pp.filter_genes(adata, min_cells=1)
        print(f"After gene filter: {adata.n_obs} cells x {adata.n_vars} genes")

    # Normalize
    sc.pp.normalize_total(adata)
    sc.pp.log1p(adata)
    print("Applied normalization and log1p transform")

    print(f"\nCell type distribution:\n{adata.obs[CELLTYPE_KEY].value_counts()}")

    # -------------------------------------------------------------------------
    # Step 3: Compute gene selection scores
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 3: Computing gene selection scores")
    print("=" * 60)

    scores_dict = {}

    # HVG
    hvg_scores = score_hvg(adata, n_top_genes=1000)
    scores_dict["hvg"] = hvg_scores

    # Differential Expression
    de_scores = score_differential_expression(adata, label_key=CELLTYPE_KEY, collapse=True)
    scores_dict["differential_expression"] = de_scores

    # Random Forest
    rf_scores = score_random_forest(adata, label_key=CELLTYPE_KEY)
    scores_dict["random_forest"] = rf_scores

    # scGeneFit
    scgf_scores = score_scgenefit(adata, label_key=CELLTYPE_KEY, method="centers")
    scores_dict["scgenefit"] = scgf_scores

    # -------------------------------------------------------------------------
    # Step 4: Merge scores into single DataFrame
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 4: Merging scores")
    print("=" * 60)

    merged = None
    for method, df in scores_dict.items():
        df_renamed = df[["gene", "score"]].rename(columns={"score": f"{method}_score"})
        merged = df_renamed if merged is None else pd.merge(merged, df_renamed, on="gene", how="outer")

    merged.fillna(0, inplace=True)

    # Filter to valid genes
    valid_genes = set(adata.var_names)
    scores_df = merged[merged["gene"].isin(valid_genes)].copy()
    print(f"Merged scores: {scores_df.shape}")

    # -------------------------------------------------------------------------
    # Step 5: Normalize scores
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 5: Normalizing scores")
    print("=" * 60)

    score_cols = [f"{m}_score" for m in METHODS]
    scores_df = normalize_scores(scores_df, score_cols)

    # -------------------------------------------------------------------------
    # Step 6: Compute reliability weights and meta-vote
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 6: Computing reliability weights and meta-vote")
    print("=" * 60)

    reliability, weights, per_method_topk, union_set = compute_reliability_weights(
        adata, scores_df, METHODS,
        label_key=CELLTYPE_KEY, top_k=1000,
        resolution=1, alpha=0.8, K_target=1000, K_max=2000, beta=1.5
    )

    meta = meta_vote(
        scores_df=scores_df, methods=METHODS,
        weights=weights, per_method_topk=per_method_topk,
        sigma=-1  # Use sigma=-1 to keep all genes in union
    )

    # -------------------------------------------------------------------------
    # Step 7: Save outputs
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 7: Saving outputs")
    print("=" * 60)

    # Save preprocessed adata
    adata_path = os.path.join(OUTPUT_DIR, "adata_preprocessed.h5ad")
    adata.write_h5ad(adata_path)
    print(f"Saved preprocessed adata to: {adata_path}")

    # Save scores as pickle
    scores_output = {
        "scores_df": scores_df,
        "raw_scores": scores_dict,
        "reliability": reliability,
        "weights": weights,
        "per_method_topk": per_method_topk,
        "union_set": union_set,
        "meta_vote_result": meta,
        "methods": METHODS,
        "config": {
            "tissue": TISSUE,
            "celltype_key": CELLTYPE_KEY,
            "data_path": DATA_PATH,
        }
    }

    scores_path = os.path.join(OUTPUT_DIR, "scores.pkl")
    with open(scores_path, "wb") as f:
        pickle.dump(scores_output, f)
    print(f"Saved scores to: {scores_path}")

    # Also save scores as CSV for easy inspection
    csv_path = os.path.join(OUTPUT_DIR, "scores.csv")
    scores_df.to_csv(csv_path, index=False)
    print(f"Saved scores CSV to: {csv_path}")

    print("\n" + "=" * 60)
    print("PREPROCESSING COMPLETE")
    print("=" * 60)
    print(f"\nOutputs:")
    print(f"  - Preprocessed adata: {adata_path}")
    print(f"  - Scores pickle: {scores_path}")
    print(f"  - Scores CSV: {csv_path}")
    print(f"\nData summary:")
    print(f"  - Cells: {adata.n_obs}")
    print(f"  - Genes: {adata.n_vars}")
    print(f"  - Cell types: {adata.obs[CELLTYPE_KEY].nunique()}")
    print(f"  - Union gene set size: {len(union_set)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Preprocess data for gene panel selection evolution"
    )
    parser.add_argument(
        "--downsample_cell_number",
        type=int,
        default=None,
        help="Downsample to this number of cells (default: no downsampling)"
    )
    args = parser.parse_args()

    main(downsample_cell_number=args.downsample_cell_number)
