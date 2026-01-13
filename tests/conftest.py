import pytest
import sys
import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Test env hardening (macOS sandbox / read-only HOME)
#
# Some scientific Python deps (scanpy/umap/pynndescent) use numba caching and
# will fail to import if their default cache locations are not writable.
# We point caches at a writable temp directory so integration tests can run.
# ---------------------------------------------------------------------------
_PANTHEON_TEST_CACHE_ROOT = Path(
    os.environ.get("PANTHEON_TEST_CACHE_DIR", "")
    or (Path(tempfile.gettempdir()) / "pantheon-tests-cache")
)
_PANTHEON_TEST_CACHE_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("NUMBA_CACHE_DIR", str(_PANTHEON_TEST_CACHE_ROOT / "numba"))
os.environ.setdefault("XDG_CACHE_HOME", str(_PANTHEON_TEST_CACHE_ROOT / "xdg"))
os.environ.setdefault("MPLCONFIGDIR", str(_PANTHEON_TEST_CACHE_ROOT / "mpl"))

# Check if scanpy is available for integration tests
try:
    import scanpy as sc
    import numpy as np
    HAS_SCANPY = True
except Exception:
    HAS_SCANPY = False

# Check if sklearn is available
try:
    from sklearn.metrics import silhouette_score
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


@pytest.fixture(scope="session", autouse=True)
def global_setup():
    """Global test setup: load environment variables and configure logging"""

    # Load environment variables from .env files
    # Priority: .env.test > .env (test-specific overrides development)
    env_files = [
        Path(__file__).parent.parent / ".env",      # Development environment
        Path(__file__).parent.parent / ".env.test",  # Test-specific overrides
    ]

    for env_file in env_files:
        if env_file.exists():
            try:
                # Manual .env file parsing (no external dependency)
                with open(env_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if not line or line.startswith('#'):
                            continue
                        # Parse KEY=VALUE format
                        if '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            # Only set if not already set (allows CLI override)
                            if key and not os.environ.get(key):
                                os.environ[key] = value
            except Exception as e:
                print(f"Warning: Failed to load {env_file}: {e}")

    # Configure logging
    import logging
    logging.basicConfig(level=logging.DEBUG)
    import loguru
    loguru.logger.remove()
    loguru.logger.add(sys.stderr, level="DEBUG")


# =============================================================================
# SCFM Test Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def scfm_toolset():
    """Create SCFMToolSet instance for testing"""
    from pantheon.toolsets.scfm import SCFMToolSet
    return SCFMToolSet(name="scfm_test")


@pytest.fixture(scope="module")
def test_adata_path(tmp_path_factory):
    """
    Create a small synthetic AnnData file for testing.

    Returns path to a temporary .h5ad file with:
    - 100 cells
    - 200 genes (human gene symbols)
    - Random expression data
    - celltype and batch columns in obs
    """
    if not HAS_SCANPY:
        pytest.skip("scanpy not installed")

    import scanpy as sc
    import numpy as np

    # Create synthetic data
    n_cells = 100
    n_genes = 200

    # Generate random expression matrix
    np.random.seed(42)
    X = np.random.poisson(lam=2, size=(n_cells, n_genes)).astype(np.float32)

    # Create gene names (human-style symbols)
    gene_names = [f"GENE{i}" for i in range(n_genes)]

    # Create cell barcodes
    cell_names = [f"CELL_{i:04d}" for i in range(n_cells)]

    # Create AnnData
    adata = sc.AnnData(X=X)
    adata.var_names = gene_names
    adata.obs_names = cell_names

    # Add cell type labels (3 types)
    adata.obs["celltype"] = np.random.choice(
        ["TypeA", "TypeB", "TypeC"], size=n_cells
    )

    # Add batch info
    adata.obs["batch"] = np.random.choice(["batch1", "batch2"], size=n_cells)

    # Add species info
    adata.uns["species"] = "human"

    # Save to temporary file
    tmp_dir = tmp_path_factory.mktemp("scfm_test")
    adata_path = tmp_dir / "test_data.h5ad"
    adata.write(str(adata_path))

    return str(adata_path)


@pytest.fixture(scope="module")
def test_adata_with_embeddings(tmp_path_factory):
    """
    Create an AnnData file with mock foundation model embeddings.

    Returns path to a temporary .h5ad file with:
    - 100 cells with celltype labels
    - Mock X_uce embeddings in obsm
    - Provenance info in uns
    """
    if not HAS_SCANPY:
        pytest.skip("scanpy not installed")

    import scanpy as sc
    import numpy as np

    # Create synthetic data
    n_cells = 100
    n_genes = 200
    embedding_dim = 1280

    np.random.seed(42)
    X = np.random.poisson(lam=2, size=(n_cells, n_genes)).astype(np.float32)

    adata = sc.AnnData(X=X)
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.obs_names = [f"CELL_{i:04d}" for i in range(n_cells)]

    # Add cell type labels - create clustered embeddings for better silhouette
    cell_types = np.array(["TypeA"] * 40 + ["TypeB"] * 30 + ["TypeC"] * 30)
    adata.obs["celltype"] = cell_types

    # Create mock embeddings with cluster structure
    # TypeA centered at [1, 0, 0, ...]
    # TypeB centered at [0, 1, 0, ...]
    # TypeC centered at [0, 0, 1, ...]
    embeddings = np.random.randn(n_cells, embedding_dim).astype(np.float32) * 0.1

    for i, ct in enumerate(cell_types):
        if ct == "TypeA":
            embeddings[i, 0] += 1.0
        elif ct == "TypeB":
            embeddings[i, 1] += 1.0
        else:
            embeddings[i, 2] += 1.0

    adata.obsm["X_uce"] = embeddings

    # Add provenance
    adata.uns["scfm"] = {
        "latest": {
            "model_name": "uce",
            "version": "4-layer",
            "task": "embed",
            "output_keys": ["obsm['X_uce']"],
            "timestamp": "2025-01-01T00:00:00",
            "backend": "local",
        }
    }

    # Save to temporary file
    tmp_dir = tmp_path_factory.mktemp("scfm_test_emb")
    adata_path = tmp_dir / "test_data_with_embeddings.h5ad"
    adata.write(str(adata_path))

    return str(adata_path)
