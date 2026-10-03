"""Compatibility alias; Playground is owned by its App package."""
import sys
from pantheon.apps.builtin.llm_playground import engine as _implementation
sys.modules[__name__] = _implementation
