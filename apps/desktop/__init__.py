"""Desktop toolset — the user's desktop, and everything on it.

The pod owns the desktop: which windows are open (``desktop_session.py``),
what each one shows, the shared browser, and the data server that hands
workspace files to the apps. The agent and every open viewport are clients of
that, not owners of it.

Named ``live_view`` until it outgrew the name. A "live view" was one
agent-driven component in the chat sidebar; what this became is a machine with
a screen, and calling it after its first feature made every error message
about the desktop mention a thing the user had never opened.
"""

from .toolset import DesktopToolSet

# The old name, for code that has not been redeployed yet. Endpoint routing has
# its own alias (endpoint/toolsets.py, TOOLSET_ALIASES).
LiveViewToolSet = DesktopToolSet

__all__ = ["DesktopToolSet", "LiveViewToolSet"]
