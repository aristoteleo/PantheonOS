"""Compatibility alias for shared Agent session recovery."""
import sys
from pantheon.internal.memory import conversation_recovery as _implementation

sys.modules[__name__] = _implementation
