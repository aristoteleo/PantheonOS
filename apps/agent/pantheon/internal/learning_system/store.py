"""Compatibility alias for the shared skill content layer."""
import sys
from pantheon.skills import store as _implementation
sys.modules[__name__] = _implementation
