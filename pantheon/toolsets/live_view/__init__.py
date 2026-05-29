"""LiveView toolset — agent-controllable in-page UI components.

A LiveView is a UI component (e.g. a Vitessce spatial-omics browser) that the
agent can open, drive, and observe. The backend holds the authoritative view
state; the agent and the UI are both clients of it.
"""

from .toolset import LiveViewToolSet

__all__ = ["LiveViewToolSet"]
