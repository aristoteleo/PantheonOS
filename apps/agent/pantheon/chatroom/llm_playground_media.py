"""Compatibility alias; Playground is owned by its App package."""
import sys
from pantheonos.appkit.builtin.llm_playground import media as _implementation
sys.modules[__name__] = _implementation
