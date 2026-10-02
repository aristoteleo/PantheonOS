"""Compatibility alias for the platform-owned project registry."""

import sys
from pantheon.platform import projects as _implementation

sys.modules[__name__] = _implementation
