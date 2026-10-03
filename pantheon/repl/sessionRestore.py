"""Compatibility alias for shared Agent session recovery."""
import sys
from pantheon.internal.memory import session_restore as _implementation

sys.modules[__name__] = _implementation
