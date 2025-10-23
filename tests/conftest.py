import pytest
import sys
import os
from pathlib import Path

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
