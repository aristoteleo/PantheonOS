"""Compatibility import; shared snapshots are owned by the platform."""
import sys
from pantheon.platform import state_sync as _implementation
sys.modules[__name__] = _implementation
