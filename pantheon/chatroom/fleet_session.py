"""Compatibility import; Fleet credentials are owned by the platform."""
import sys
from pantheon.platform import fleet_session as _implementation
sys.modules[__name__] = _implementation
