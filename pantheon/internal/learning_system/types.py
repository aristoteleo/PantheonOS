"""Compatibility alias for the shared skill content layer."""
import sys
from pantheon.skills import types as _implementation
sys.modules[__name__] = _implementation
