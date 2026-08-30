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


