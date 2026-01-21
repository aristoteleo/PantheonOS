#!/usr/bin/env python
"""
Generate publication-quality PDF figures comparing evolved algorithms vs original on TMA data.

This script supports multiple batch correction algorithms:
- BBKNN
- Scanorama
- Harmony

Usage:
    python plot_paper_figures.py --algorithm bbknn
    python plot_paper_figures.py --algorithm scanorama
    python plot_paper_figures.py --algorithm harmony
"""

import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl
import time
import importlib.util
import sys
from pathlib import Path
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import silhouette_score
from sklearn.manifold import SpectralEmbedding

# Setup for publication-quality figures
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'],
    'font.size': 10,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 9,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.linewidth': 1.0,
    'lines.linewidth': 1.5,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'pdf.fonttype': 42,  # TrueType fonts for editability
    'ps.fonttype': 42,
})

# Setup paths
example_dir = Path(__file__).parent
data_dir = example_dir / "data"


def load_module(name: str, path: Path):
    """Load a Python module from file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_tma_data(split: str = "test"):
    """Load TMA data with real cell type labels."""
    df = pd.read_csv(data_dir / f"tma_8000_{split}.csv")
    X = df.iloc[:, :30].values  # PC1-PC30
    batch_labels = df["donor"].values
    cell_types = df["celltype"].values
    return X, batch_labels, cell_types


def load_ffdaa1f0_data(split: str = "validation"):
    """Load ffdaa1f0 heart data with cell type labels."""
    split_name = "training" if split == "train" else split
    obs_df = pd.read_csv(data_dir / f"ffdaa1f0_{split_name}_obs.csv", index_col=0)
    pca_df = pd.read_csv(data_dir / f"ffdaa1f0_{split_name}_pca.csv", header=None)
    X = pca_df.values  # 50 PCA dimensions
    batch_labels = obs_df["donor_id"].values
    cell_types = obs_df["cell_type"].values
    return X, batch_labels, cell_types


def load_pbmc_data(split: str = "val"):
    """Load PBMC data. Uses k-means clustering for pseudo cell types."""
    from sklearn.cluster import KMeans

    split_name = "train" if split == "train" else "val"
    df = pd.read_csv(data_dir / f"pbmc_3500_full_{split_name}.csv")
    X = df.iloc[:, :30].values  # PC1-PC30
    batch_labels = df["donor"].values

    # Generate pseudo cell type labels using k-means clustering
    # PBMC typically has ~7-10 major cell types
    kmeans = KMeans(n_clusters=8, random_state=42, n_init=10)
    cell_types = kmeans.fit_predict(X).astype(str)

    return X, batch_labels, cell_types


def compute_batch_mixing_score(X: np.ndarray, batch_labels: np.ndarray, k: int = 50) -> float:
    """Compute batch mixing score using k-nearest neighbors."""
    n_cells = X.shape[0]
    unique_batches = np.unique(batch_labels)
    expected_props = np.array([np.sum(batch_labels == b) / n_cells for b in unique_batches])

    nn = NearestNeighbors(n_neighbors=min(k + 1, n_cells), algorithm="auto")
    nn.fit(X)
    _, indices = nn.kneighbors(X)

    mixing_scores = []
    for i in range(n_cells):
        neighbor_batches = batch_labels[indices[i, 1:]]
        observed_props = np.array([np.sum(neighbor_batches == b) / k for b in unique_batches])
        score = 1 - np.sqrt(np.mean((observed_props - expected_props) ** 2))
        mixing_scores.append(max(0, score))

    return np.mean(mixing_scores)


def compute_bio_conservation_score(X: np.ndarray, labels: np.ndarray) -> float:
    """Compute biological structure conservation using silhouette score."""
    try:
        if len(np.unique(labels)) > 1:
            silhouette = silhouette_score(X, labels)
            return (silhouette + 1) / 2
        return 0.5
    except Exception:
        return 0.5


def graph_to_embedding(connectivities, n_components: int = 30) -> np.ndarray:
    """Convert a connectivity graph to an embedding using spectral embedding."""
    conn_sym = (connectivities + connectivities.T) / 2
    se = SpectralEmbedding(
        n_components=n_components,
        affinity='precomputed',
        random_state=42,
    )
    embedding = se.fit_transform(conn_sym.toarray())
    return embedding


# =============================================================================
# BBKNN-specific functions
# =============================================================================

def load_bbknn_module(bbknn_path: Path):
    """Load the BBKNN matrix module."""
    matrix_path = bbknn_path / "matrix.py"
    spec_matrix = importlib.util.spec_from_file_location("bbknn.matrix", matrix_path)
    matrix_module = importlib.util.module_from_spec(spec_matrix)
    sys.modules["bbknn.matrix"] = matrix_module
    spec_matrix.loader.exec_module(matrix_module)
    return matrix_module


def run_bbknn(module, X: np.ndarray, batch_labels: np.ndarray, n_pcs: int = 30):
    """Run BBKNN and return corrected embedding."""
    distances, connectivities, params = module.bbknn(
        pca=X,
        batch_list=batch_labels,
        neighbors_within_batch=3,
        n_pcs=n_pcs,
        computation='cKDTree',
        metric='euclidean',
    )
    return graph_to_embedding(connectivities, n_components=min(30, n_pcs))


# =============================================================================
# Figure generation
# =============================================================================

def generate_bbknn_figures(dataset: str = "tma"):
    """Generate publication-quality PDF figures for BBKNN."""
    from umap import UMAP

    if dataset == "tma":
        print("Loading TMA TEST data (held-out evaluation)...")
        X, batch_labels, cell_types = load_tma_data("test")
        n_pcs = 30
    elif dataset == "ffdaa1f0":
        print("Loading ffdaa1f0 VALIDATION data...")
        X, batch_labels, cell_types = load_ffdaa1f0_data("validation")
        n_pcs = 50
    elif dataset == "pbmc":
        print("Loading PBMC VALIDATION data...")
        X, batch_labels, cell_types = load_pbmc_data("val")
        n_pcs = 30
    else:
        raise ValueError(f"Unknown dataset: {dataset}")
    print(f"  Data shape: {X.shape}")

    # Load BBKNN implementations
    print("\nLoading BBKNN implementations...")
    bbknn_dir = example_dir / "evolution_bbknn"
    bbknn_original = load_bbknn_module(bbknn_dir / "bbknn")

    # Run original BBKNN
    print("\nRunning original BBKNN...")
    start_time = time.time()
    X_corrected_original = run_bbknn(bbknn_original, X, batch_labels, n_pcs)
    time_original = time.time() - start_time
    print(f"  Time: {time_original:.2f}s")

    # Run BBKNN #71 (intermediate evolved version)
    sys.modules.pop("bbknn.matrix", None)
    bbknn_71 = load_bbknn_module(bbknn_dir / "results" / "bbknn_71")

    print("\nRunning BBKNN (#71)...")
    start_time = time.time()
    X_corrected_71 = run_bbknn(bbknn_71, X, batch_labels, n_pcs)
    time_71 = time.time() - start_time
    print(f"  Time: {time_71:.2f}s")

    # Run evolved BBKNN (best)
    sys.modules.pop("bbknn.matrix", None)
    bbknn_evolved = load_bbknn_module(bbknn_dir / "results" / "bbknn_optimized")

    print("\nRunning BBKNN (Best)...")
    start_time = time.time()
    X_corrected_evolved = run_bbknn(bbknn_evolved, X, batch_labels, n_pcs)
    time_evolved = time.time() - start_time
    print(f"  Time: {time_evolved:.2f}s")

    # Compute metrics
    print("\nComputing metrics...")
    metrics = {
        "Original Data": {
            "mixing": compute_batch_mixing_score(X, batch_labels),
            "bio": compute_bio_conservation_score(X, cell_types),
            "time": 0,
        },
        "BBKNN": {
            "mixing": compute_batch_mixing_score(X_corrected_original, batch_labels),
            "bio": compute_bio_conservation_score(X_corrected_original, cell_types),
            "time": time_original,
        },
        "BBKNN (#71)": {
            "mixing": compute_batch_mixing_score(X_corrected_71, batch_labels),
            "bio": compute_bio_conservation_score(X_corrected_71, cell_types),
            "time": time_71,
        },
        "BBKNN (Best)": {
            "mixing": compute_batch_mixing_score(X_corrected_evolved, batch_labels),
            "bio": compute_bio_conservation_score(X_corrected_evolved, cell_types),
            "time": time_evolved,
        },
    }

    for name, m in metrics.items():
        print(f"  {name}: mixing={m['mixing']:.4f}, bio={m['bio']:.4f}")

    # Compute UMAP
    print("\nComputing UMAP embeddings...")
    umap = UMAP(n_neighbors=30, min_dist=0.3, random_state=42)
    umap_original = umap.fit_transform(X)
    umap_bbknn = umap.fit_transform(X_corrected_original)
    umap_71 = umap.fit_transform(X_corrected_71)
    umap_evolved = umap.fit_transform(X_corrected_evolved)

    output_dir = bbknn_dir / "results" / f"paper_figures_{dataset}"
    output_dir.mkdir(exist_ok=True)

    # Define batch colors
    unique_batches = np.unique(batch_labels)
    n_batches = len(unique_batches)
    if n_batches == 2 and set(unique_batches) == {'10x', 'SS2'}:
        batch_colors = {'10x': '#E64B35', 'SS2': '#4DBBD5'}  # Nature-style colors for TMA
    else:
        # Use a colormap for many batches
        batch_cmap = plt.cm.get_cmap('tab20', min(n_batches, 20))
        batch_colors = {b: mpl.colors.rgb2hex(batch_cmap(i % 20)) for i, b in enumerate(unique_batches)}

    # Cell type colors
    unique_celltypes = np.unique(cell_types)
    n_types = len(unique_celltypes)
    cmap = plt.cm.get_cmap('tab20', n_types)
    celltype_colors = {ct: mpl.colors.rgb2hex(cmap(i)) for i, ct in enumerate(unique_celltypes)}

    # =========================================================================
    # Figure 1: UMAP Comparison (2x4 layout)
    # =========================================================================
    print("\nGenerating Figure 1: UMAP comparison...")
    fig1, axes = plt.subplots(2, 4, figsize=(10, 5))

    datasets = [
        (umap_original, "Uncorrected", metrics["Original Data"]),
        (umap_bbknn, "BBKNN", metrics["BBKNN"]),
        (umap_71, "BBKNN (#71)", metrics["BBKNN (#71)"]),
        (umap_evolved, "BBKNN (Best)", metrics["BBKNN (Best)"]),
    ]

    # Row 1: Color by batch
    for idx, (emb, title, m) in enumerate(datasets):
        ax = axes[0, idx]
        for batch in unique_batches:
            mask = batch_labels == batch
            ax.scatter(emb[mask, 0], emb[mask, 1], c=batch_colors[batch],
                      label=batch if n_batches <= 10 else None, s=2, alpha=0.5, rasterized=True)
        ax.set_title(f"{title}", fontweight='bold')
        ax.set_xlabel("UMAP1")
        if idx == 0:
            ax.set_ylabel("UMAP2")
        ax.text(0.02, 0.98, f"Mixing: {m['mixing']:.3f}", transform=ax.transAxes,
               va='top', ha='left', fontsize=8, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        ax.set_xticks([])
        ax.set_yticks([])

    # Add legend to first row (only if few batches)
    if n_batches <= 10:
        axes[0, 3].legend(title="Batch", loc='upper right', markerscale=3, framealpha=0.9)
    else:
        axes[0, 3].text(0.98, 0.98, f"{n_batches} batches", transform=axes[0, 3].transAxes,
                       va='top', ha='right', fontsize=8, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    # Row 2: Color by cell type
    for idx, (emb, title, m) in enumerate(datasets):
        ax = axes[1, idx]
        for ct in unique_celltypes:
            mask = cell_types == ct
            ax.scatter(emb[mask, 0], emb[mask, 1], c=celltype_colors[ct],
                      label=ct if n_types <= 20 else None, s=2, alpha=0.5, rasterized=True)
        ax.set_xlabel("UMAP1")
        if idx == 0:
            ax.set_ylabel("UMAP2")
        ax.text(0.02, 0.98, f"Bio: {m['bio']:.3f}", transform=ax.transAxes,
               va='top', ha='left', fontsize=8, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        ax.set_xticks([])
        ax.set_yticks([])

    # Add cell type legend (only if few cell types)
    if n_types <= 20:
        handles, labels = axes[1, 3].get_legend_handles_labels()
        axes[1, 3].legend(handles, labels, title="Cell Type", loc='upper right',
                          markerscale=3, framealpha=0.9, fontsize=6, ncol=2)
    else:
        axes[1, 3].text(0.98, 0.98, f"{n_types} cell types", transform=axes[1, 3].transAxes,
                       va='top', ha='right', fontsize=8, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    # Add row labels
    fig1.text(0.01, 0.75, 'Batch', va='center', ha='left', rotation=90, fontsize=10, fontweight='bold')
    fig1.text(0.01, 0.28, 'Cell Type', va='center', ha='left', rotation=90, fontsize=10, fontweight='bold')

    plt.tight_layout(rect=[0.02, 0, 1, 1])
    fig1.savefig(output_dir / "figure1_umap_comparison.pdf", format='pdf', bbox_inches='tight')
    fig1.savefig(output_dir / "figure1_umap_comparison.png", format='png', bbox_inches='tight', dpi=300)
    print(f"  Saved: {output_dir}/figure1_umap_comparison.pdf")

    # =========================================================================
    # Figure 2: Performance Bar Chart
    # =========================================================================
    print("\nGenerating Figure 2: Performance comparison...")
    fig2, axes2 = plt.subplots(1, 3, figsize=(9, 2.5))

    methods = ["Uncorrected", "BBKNN", "BBKNN\n(#71)", "BBKNN\n(Best)"]
    colors = ["#999999", "#3C5488", "#7E6148", "#00A087"]  # Nature-style

    # Mixing score
    ax = axes2[0]
    mixing_vals = [metrics["Original Data"]["mixing"], metrics["BBKNN"]["mixing"],
                   metrics["BBKNN (#71)"]["mixing"], metrics["BBKNN (Best)"]["mixing"]]
    bars = ax.bar(methods, mixing_vals, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Batch Mixing Score")
    ax.set_ylim(0, 1)
    ax.set_title("Batch Integration", fontweight='bold')
    for bar, val in zip(bars, mixing_vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.02, f'{val:.3f}',
               ha='center', va='bottom', fontsize=7)

    # Bio conservation
    ax = axes2[1]
    bio_vals = [metrics["Original Data"]["bio"], metrics["BBKNN"]["bio"],
                metrics["BBKNN (#71)"]["bio"], metrics["BBKNN (Best)"]["bio"]]
    bars = ax.bar(methods, bio_vals, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Bio Conservation Score")
    ax.set_ylim(0, 1)
    ax.set_title("Biological Structure", fontweight='bold')
    for bar, val in zip(bars, bio_vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.02, f'{val:.3f}',
               ha='center', va='bottom', fontsize=7)

    # Execution time
    ax = axes2[2]
    time_methods = ["BBKNN", "BBKNN\n(#71)", "BBKNN\n(Best)"]
    time_vals = [metrics["BBKNN"]["time"], metrics["BBKNN (#71)"]["time"], metrics["BBKNN (Best)"]["time"]]
    time_colors = ["#3C5488", "#7E6148", "#00A087"]
    bars = ax.bar(time_methods, time_vals, color=time_colors, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Execution Time (s)")
    ax.set_title("Computational Cost", fontweight='bold')
    for bar, val in zip(bars, time_vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.2, f'{val:.2f}s',
               ha='center', va='bottom', fontsize=7)

    plt.tight_layout()
    fig2.savefig(output_dir / "figure2_performance.pdf", format='pdf', bbox_inches='tight')
    fig2.savefig(output_dir / "figure2_performance.png", format='png', bbox_inches='tight', dpi=300)
    print(f"  Saved: {output_dir}/figure2_performance.pdf")

    # =========================================================================
    # Figure 3: Combined Summary Figure (single panel)
    # =========================================================================
    print("\nGenerating Figure 3: Summary figure...")
    fig3, ax3 = plt.subplots(figsize=(5, 4))

    # Scatter plot: Mixing vs Bio Conservation
    for name, m, color, marker in [
        ("Uncorrected", metrics["Original Data"], "#999999", "o"),
        ("BBKNN", metrics["BBKNN"], "#3C5488", "s"),
        ("BBKNN (#71)", metrics["BBKNN (#71)"], "#7E6148", "D"),
        ("BBKNN (Best)", metrics["BBKNN (Best)"], "#00A087", "^"),
    ]:
        ax3.scatter(m["mixing"], m["bio"], c=color, s=150, marker=marker,
                   label=name, edgecolors='black', linewidth=0.5, zorder=3)

    ax3.set_xlabel("Batch Mixing Score")
    ax3.set_ylabel("Bio Conservation Score")

    # Auto-scale axes based on data
    all_mixing = [m["mixing"] for m in metrics.values()]
    all_bio = [m["bio"] for m in metrics.values()]
    ax3.set_xlim(min(all_mixing) - 0.05, max(all_mixing) + 0.1)
    ax3.set_ylim(min(all_bio) - 0.05, max(all_bio) + 0.1)

    ax3.legend(loc='lower right', framealpha=0.9)
    ax3.grid(True, alpha=0.3, linestyle='--')

    plt.tight_layout()
    fig3.savefig(output_dir / "figure3_summary.pdf", format='pdf', bbox_inches='tight')
    fig3.savefig(output_dir / "figure3_summary.png", format='png', bbox_inches='tight', dpi=300)
    print(f"  Saved: {output_dir}/figure3_summary.pdf")

    print(f"\n{'='*60}")
    print(f"All figures saved to: {output_dir}")
    print("Files generated:")
    print("  - figure1_umap_comparison.pdf")
    print("  - figure2_performance.pdf")
    print("  - figure3_summary.pdf")
    print(f"{'='*60}")

    return metrics


# =============================================================================
# Scanorama-specific functions
# =============================================================================

def load_scanorama_module(scanorama_path: Path):
    """Load the Scanorama module."""
    # Load annoy stub first (before importing scanorama)
    annoy_stub_path = example_dir / "evolution_scanorama" / "annoy.py"
    if annoy_stub_path.exists() and "annoy" not in sys.modules:
        spec_annoy = importlib.util.spec_from_file_location("annoy", annoy_stub_path)
        annoy_module = importlib.util.module_from_spec(spec_annoy)
        sys.modules["annoy"] = annoy_module
        spec_annoy.loader.exec_module(annoy_module)

    # Load utils first
    utils_path = scanorama_path / "utils.py"
    spec_utils = importlib.util.spec_from_file_location("scanorama.utils", utils_path)
    utils_module = importlib.util.module_from_spec(spec_utils)
    sys.modules["scanorama.utils"] = utils_module
    spec_utils.loader.exec_module(utils_module)

    # Import main scanorama module
    main_path = scanorama_path / "scanorama.py"
    spec = importlib.util.spec_from_file_location("scanorama.scanorama", main_path)
    scanorama_module = importlib.util.module_from_spec(spec)
    sys.modules["scanorama.scanorama"] = scanorama_module
    spec.loader.exec_module(scanorama_module)

    return scanorama_module


def split_by_batch(X: np.ndarray, batch_labels: np.ndarray):
    """Split data by batch for Scanorama input."""
    unique_batches = np.unique(batch_labels)
    datasets = []
    batch_names = []
    original_order = []

    for batch in unique_batches:
        mask = batch_labels == batch
        datasets.append(X[mask])
        batch_names.append(batch)
        original_order.append(np.where(mask)[0])

    return datasets, batch_names, original_order


def reconstruct_from_batches(datasets_corrected, original_order):
    """Reconstruct full matrix from batch-wise corrected data."""
    n_total = sum(len(order) for order in original_order)
    n_features = datasets_corrected[0].shape[1]
    X_reconstructed = np.zeros((n_total, n_features))

    for dataset, order in zip(datasets_corrected, original_order):
        X_reconstructed[order] = dataset

    return X_reconstructed


def run_scanorama(module, X: np.ndarray, batch_labels: np.ndarray):
    """Run Scanorama and return corrected embedding."""
    datasets, batch_names, original_order = split_by_batch(X, batch_labels)

    # Create fake gene names (Scanorama expects them)
    n_features = X.shape[1]
    genes_list = [[f"PC{i+1}" for i in range(n_features)] for _ in datasets]

    # Run Scanorama integrate
    datasets_corrected, _ = module.integrate(
        datasets,
        genes_list,
        dimred=30,
        verbose=0,
        knn=20,
        sigma=15,
        alpha=0.10,
        approx=False,
        seed=42,
    )

    return reconstruct_from_batches(datasets_corrected, original_order)


def generate_scanorama_figures(dataset: str = "tma"):
    """Generate publication-quality PDF figures for Scanorama."""
    from umap import UMAP

    if dataset == "tma":
        print("Loading TMA TEST data (held-out evaluation)...")
        X, batch_labels, cell_types = load_tma_data("test")
    elif dataset == "ffdaa1f0":
        print("Loading ffdaa1f0 VALIDATION data...")
        X, batch_labels, cell_types = load_ffdaa1f0_data("validation")
    elif dataset == "pbmc":
        print("Loading PBMC VALIDATION data...")
        X, batch_labels, cell_types = load_pbmc_data("val")
    else:
        raise ValueError(f"Unknown dataset: {dataset}")
    print(f"  Data shape: {X.shape}")

    # Load Scanorama implementations
    print("\nLoading Scanorama implementations...")
    scanorama_dir = example_dir / "evolution_scanorama"
    scanorama_original = load_scanorama_module(scanorama_dir / "scanorama")

    # Run original Scanorama
    print("\nRunning original Scanorama...")
    start_time = time.time()
    X_corrected_original = run_scanorama(scanorama_original, X, batch_labels)
    time_original = time.time() - start_time
    print(f"  Time: {time_original:.2f}s")

    # Run evolved Scanorama (best)
    # Clear module cache
    for key in list(sys.modules.keys()):
        if 'scanorama' in key:
            del sys.modules[key]

    scanorama_evolved = load_scanorama_module(scanorama_dir / "results" / "scanorama_optimized")

    print("\nRunning Scanorama (Best)...")
    start_time = time.time()
    X_corrected_evolved = run_scanorama(scanorama_evolved, X, batch_labels)
    time_evolved = time.time() - start_time
    print(f"  Time: {time_evolved:.2f}s")

    # Compute metrics
    print("\nComputing metrics...")
    metrics = {
        "Original Data": {
            "mixing": compute_batch_mixing_score(X, batch_labels),
            "bio": compute_bio_conservation_score(X, cell_types),
            "time": 0,
        },
        "Scanorama": {
            "mixing": compute_batch_mixing_score(X_corrected_original, batch_labels),
            "bio": compute_bio_conservation_score(X_corrected_original, cell_types),
            "time": time_original,
        },
        "Scanorama (Best)": {
            "mixing": compute_batch_mixing_score(X_corrected_evolved, batch_labels),
            "bio": compute_bio_conservation_score(X_corrected_evolved, cell_types),
            "time": time_evolved,
        },
    }

    for name, m in metrics.items():
        print(f"  {name}: mixing={m['mixing']:.4f}, bio={m['bio']:.4f}")

    # Compute UMAP
    print("\nComputing UMAP embeddings...")
    umap = UMAP(n_neighbors=30, min_dist=0.3, random_state=42)
    umap_original = umap.fit_transform(X)
    umap_scanorama = umap.fit_transform(X_corrected_original)
    umap_evolved = umap.fit_transform(X_corrected_evolved)

    output_dir = scanorama_dir / "results" / f"paper_figures_{dataset}"
    output_dir.mkdir(exist_ok=True)

    # Define batch colors
    unique_batches = np.unique(batch_labels)
    n_batches = len(unique_batches)
    if n_batches == 2 and set(unique_batches) == {'10x', 'SS2'}:
        batch_colors = {'10x': '#E64B35', 'SS2': '#4DBBD5'}
    else:
        batch_cmap = plt.cm.get_cmap('tab20', min(n_batches, 20))
        batch_colors = {b: mpl.colors.rgb2hex(batch_cmap(i % 20)) for i, b in enumerate(unique_batches)}

    # Cell type colors
    unique_celltypes = np.unique(cell_types)
    n_types = len(unique_celltypes)
    cmap = plt.cm.get_cmap('tab20', n_types)
    celltype_colors = {ct: mpl.colors.rgb2hex(cmap(i)) for i, ct in enumerate(unique_celltypes)}

    # =========================================================================
    # Figure 1: UMAP Comparison (2x3 layout)
    # =========================================================================
    print("\nGenerating Figure 1: UMAP comparison...")
    fig1, axes = plt.subplots(2, 3, figsize=(8, 5))

    datasets_plot = [
        (umap_original, "Uncorrected", metrics["Original Data"]),
        (umap_scanorama, "Scanorama", metrics["Scanorama"]),
        (umap_evolved, "Scanorama (Best)", metrics["Scanorama (Best)"]),
    ]

    # Row 1: Color by batch
    for idx, (emb, title, m) in enumerate(datasets_plot):
        ax = axes[0, idx]
        for batch in unique_batches:
            mask = batch_labels == batch
            ax.scatter(emb[mask, 0], emb[mask, 1], c=batch_colors[batch],
                      label=batch if n_batches <= 10 else None, s=2, alpha=0.5, rasterized=True)
        ax.set_title(f"{title}", fontweight='bold')
        ax.set_xlabel("UMAP1")
        if idx == 0:
            ax.set_ylabel("UMAP2")
        ax.text(0.02, 0.98, f"Mixing: {m['mixing']:.3f}", transform=ax.transAxes,
               va='top', ha='left', fontsize=8, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        ax.set_xticks([])
        ax.set_yticks([])

    if n_batches <= 10:
        axes[0, 2].legend(title="Batch", loc='upper right', markerscale=3, framealpha=0.9)

    # Row 2: Color by cell type
    for idx, (emb, title, m) in enumerate(datasets_plot):
        ax = axes[1, idx]
        for ct in unique_celltypes:
            mask = cell_types == ct
            ax.scatter(emb[mask, 0], emb[mask, 1], c=celltype_colors[ct],
                      label=ct if n_types <= 20 else None, s=2, alpha=0.5, rasterized=True)
        ax.set_xlabel("UMAP1")
        if idx == 0:
            ax.set_ylabel("UMAP2")
        ax.text(0.02, 0.98, f"Bio: {m['bio']:.3f}", transform=ax.transAxes,
               va='top', ha='left', fontsize=8, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        ax.set_xticks([])
        ax.set_yticks([])

    if n_types <= 20:
        handles, labels = axes[1, 2].get_legend_handles_labels()
        axes[1, 2].legend(handles, labels, title="Cell Type", loc='upper right',
                          markerscale=3, framealpha=0.9, fontsize=6, ncol=2)

    fig1.text(0.01, 0.75, 'Batch', va='center', ha='left', rotation=90, fontsize=10, fontweight='bold')
    fig1.text(0.01, 0.28, 'Cell Type', va='center', ha='left', rotation=90, fontsize=10, fontweight='bold')

    plt.tight_layout(rect=[0.02, 0, 1, 1])
    fig1.savefig(output_dir / "figure1_umap_comparison.pdf", format='pdf', bbox_inches='tight')
    fig1.savefig(output_dir / "figure1_umap_comparison.png", format='png', bbox_inches='tight', dpi=300)
    print(f"  Saved: {output_dir}/figure1_umap_comparison.pdf")

    # =========================================================================
    # Figure 2: Performance Bar Chart
    # =========================================================================
    print("\nGenerating Figure 2: Performance comparison...")
    fig2, axes2 = plt.subplots(1, 3, figsize=(8, 2.5))

    methods = ["Uncorrected", "Scanorama", "Scanorama\n(Best)"]
    colors = ["#999999", "#3C5488", "#00A087"]

    # Mixing score
    ax = axes2[0]
    mixing_vals = [metrics["Original Data"]["mixing"], metrics["Scanorama"]["mixing"],
                   metrics["Scanorama (Best)"]["mixing"]]
    bars = ax.bar(methods, mixing_vals, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Batch Mixing Score")
    ax.set_ylim(0, 1)
    ax.set_title("Batch Integration", fontweight='bold')
    for bar, val in zip(bars, mixing_vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.02, f'{val:.3f}',
               ha='center', va='bottom', fontsize=7)

    # Bio conservation
    ax = axes2[1]
    bio_vals = [metrics["Original Data"]["bio"], metrics["Scanorama"]["bio"],
                metrics["Scanorama (Best)"]["bio"]]
    bars = ax.bar(methods, bio_vals, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Bio Conservation Score")
    ax.set_ylim(0, 1)
    ax.set_title("Biological Structure", fontweight='bold')
    for bar, val in zip(bars, bio_vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.02, f'{val:.3f}',
               ha='center', va='bottom', fontsize=7)

    # Execution time
    ax = axes2[2]
    time_methods = ["Scanorama", "Scanorama\n(Best)"]
    time_vals = [metrics["Scanorama"]["time"], metrics["Scanorama (Best)"]["time"]]
    time_colors = ["#3C5488", "#00A087"]
    bars = ax.bar(time_methods, time_vals, color=time_colors, edgecolor='black', linewidth=0.5)
    ax.set_ylabel("Execution Time (s)")
    ax.set_title("Computational Cost", fontweight='bold')
    for bar, val in zip(bars, time_vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.2, f'{val:.2f}s',
               ha='center', va='bottom', fontsize=7)

    plt.tight_layout()
    fig2.savefig(output_dir / "figure2_performance.pdf", format='pdf', bbox_inches='tight')
    fig2.savefig(output_dir / "figure2_performance.png", format='png', bbox_inches='tight', dpi=300)
    print(f"  Saved: {output_dir}/figure2_performance.pdf")

    # =========================================================================
    # Figure 3: Summary Figure
    # =========================================================================
    print("\nGenerating Figure 3: Summary figure...")
    fig3, ax3 = plt.subplots(figsize=(5, 4))

    for name, m, color, marker in [
        ("Uncorrected", metrics["Original Data"], "#999999", "o"),
        ("Scanorama", metrics["Scanorama"], "#3C5488", "s"),
        ("Scanorama (Best)", metrics["Scanorama (Best)"], "#00A087", "^"),
    ]:
        ax3.scatter(m["mixing"], m["bio"], c=color, s=150, marker=marker,
                   label=name, edgecolors='black', linewidth=0.5, zorder=3)

    ax3.set_xlabel("Batch Mixing Score")
    ax3.set_ylabel("Bio Conservation Score")

    all_mixing = [m["mixing"] for m in metrics.values()]
    all_bio = [m["bio"] for m in metrics.values()]
    ax3.set_xlim(min(all_mixing) - 0.05, max(all_mixing) + 0.1)
    ax3.set_ylim(min(all_bio) - 0.05, max(all_bio) + 0.1)

    ax3.legend(loc='lower right', framealpha=0.9)
    ax3.grid(True, alpha=0.3, linestyle='--')

    plt.tight_layout()
    fig3.savefig(output_dir / "figure3_summary.pdf", format='pdf', bbox_inches='tight')
    fig3.savefig(output_dir / "figure3_summary.png", format='png', bbox_inches='tight', dpi=300)
    print(f"  Saved: {output_dir}/figure3_summary.pdf")

    print(f"\n{'='*60}")
    print(f"All figures saved to: {output_dir}")
    print(f"{'='*60}")

    return metrics


def plot_evolution_fitness(algorithm: str = "bbknn"):
    """Plot evolution fitness progress curve with recalculated scores using final metric_ranges."""
    import json

    if algorithm == "bbknn":
        results_dir = example_dir / "evolution_bbknn" / "results"
    elif algorithm == "scanorama":
        results_dir = example_dir / "evolution_scanorama" / "results"
    elif algorithm == "harmony":
        results_dir = example_dir / "evolution_harmonypy" / "results"
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")

    # Load metadata with final metric_ranges
    with open(results_dir / "metadata.json") as f:
        metadata = json.load(f)
    metric_ranges = metadata.get("metric_ranges", {})

    # Default weights
    weights = {"mixing_score": 0.45, "bio_conservation_score": 0.45, "speed_score": 0.10}

    def calc_fitness(metrics, ranges, weights):
        """Calculate fitness using final metric_ranges for consistent comparison."""
        score = 0
        for dim, weight in weights.items():
            val = metrics.get(dim, 0)
            r = ranges.get(dim, [0, 1])
            if r[1] > r[0]:
                normalized = (val - r[0]) / (r[1] - r[0])
            else:
                normalized = 0.5
            normalized = max(0, min(1, normalized))
            score += weight * normalized
        return score

    # Load all programs and recalculate fitness
    programs_dir = results_dir / "programs"
    programs = []
    for p in programs_dir.glob("*.json"):
        with open(p) as f:
            prog = json.load(f)
            programs.append(prog)

    # Sort by order and recalculate fitness
    programs_sorted = sorted(programs, key=lambda x: x.get("order", 0))

    orders = []
    recalc_scores = []
    for prog in programs_sorted:
        m = prog.get("metrics", {})
        if m and "mixing_score" in m:
            fitness = calc_fitness(m, metric_ranges, weights)
            orders.append(prog["order"])
            recalc_scores.append(fitness)

    # Calculate cumulative best score
    best_scores = []
    current_best = 0
    for score in recalc_scores:
        current_best = max(current_best, score)
        best_scores.append(current_best)

    # Find key iterations
    best_score = max(recalc_scores)
    best_idx = recalc_scores.index(best_score)
    best_order = orders[best_idx]

    # Find #71 if exists (only for BBKNN)
    iter_71_idx = None
    iter_71_score = None
    if algorithm == "bbknn":
        for i, order in enumerate(orders):
            if order == 71:
                iter_71_idx = i
                iter_71_score = recalc_scores[i]
                break

    # Create figure
    fig, ax = plt.subplots(figsize=(8, 5))

    # Plot best score progression
    ax.plot(orders, best_scores, color='#E64B35', linewidth=2,
            label='Best Score', zorder=3)

    # Mark the best iteration with vertical line
    ax.axvline(x=best_order, color='#00A087', linestyle='--', alpha=0.7)
    ax.scatter([best_order], [best_score], s=100, c='#00A087',
               marker='^', edgecolors='black', linewidth=1, zorder=4,
               label=f'Best #{best_order} ({best_score:.4f})')

    # Mark #71 if exists (BBKNN only)
    if iter_71_idx is not None:
        ax.axvline(x=71, color='#7E6148', linestyle='--', alpha=0.7)
        ax.scatter([71], [iter_71_score], s=100, c='#7E6148', marker='D',
                   edgecolors='black', linewidth=1, zorder=4,
                   label=f'#71 ({iter_71_score:.4f})')

    ax.set_xlabel("Iteration", fontsize=11)
    ax.set_ylabel("Fitness Score (rescaled)", fontsize=11)
    ax.set_title(f"{algorithm.upper()} Evolution Progress", fontweight='bold', fontsize=12)
    ax.legend(loc='lower right', framealpha=0.9)
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_xlim(0, max(orders) + 5)
    # Y-axis starts from initial (minimum) score
    min_score = min(best_scores)  # Initial score is the first best_score
    ax.set_ylim(min_score - 0.02, 1.02)

    # Add statistics text
    stats_text = (f"Total programs: {len(orders)}\n"
                  f"Best score: {best_score:.4f}\n"
                  f"Best iteration: #{best_order}")
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, va='top', ha='left',
            fontsize=9, bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    plt.tight_layout()

    # Save figures
    output_dir = results_dir / "paper_figures_tma"
    output_dir.mkdir(exist_ok=True)
    fig.savefig(output_dir / "evolution_fitness_progress.pdf", format='pdf', bbox_inches='tight')
    fig.savefig(output_dir / "evolution_fitness_progress.png", format='png', bbox_inches='tight', dpi=300)
    print(f"Saved: {output_dir}/evolution_fitness_progress.pdf")

    # Print top programs
    print(f"\nTop 5 programs (recalculated with final metric_ranges):")
    sorted_by_fitness = sorted(zip(orders, recalc_scores), key=lambda x: -x[1])
    for order, score in sorted_by_fitness[:5]:
        print(f"  #{order}: {score:.4f}")

    plt.close(fig)
    return metadata


def main():
    parser = argparse.ArgumentParser(
        description="Generate publication-quality figures for evolved batch correction algorithms"
    )
    parser.add_argument(
        "--algorithm", "-a",
        type=str,
        default="bbknn",
        choices=["bbknn", "scanorama", "harmony"],
        help="Algorithm to evaluate (default: bbknn)",
    )
    parser.add_argument(
        "--dataset", "-d",
        type=str,
        default="tma",
        choices=["tma", "ffdaa1f0", "pbmc"],
        help="Dataset to use (default: tma)",
    )
    parser.add_argument(
        "--fitness-curve", "-f",
        action="store_true",
        help="Plot evolution fitness curve only",
    )

    args = parser.parse_args()

    if args.fitness_curve:
        plot_evolution_fitness(algorithm=args.algorithm)
    elif args.algorithm == "bbknn":
        generate_bbknn_figures(dataset=args.dataset)
    elif args.algorithm == "scanorama":
        generate_scanorama_figures(dataset=args.dataset)
    elif args.algorithm == "harmony":
        print("Harmony figure generation not yet implemented")


if __name__ == "__main__":
    main()
