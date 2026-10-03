"""Public legacy entrypoints, loaded only when explicitly requested.

Importing a domain submodule must not bootstrap the old platform composition.
"""

__all__ = ["ChatRoom", "start_services", "AgentRuntime"]


def __getattr__(name):
    if name == "ChatRoom":
        from .room import ChatRoom
        return ChatRoom
    if name == "start_services":
        from .start import start_services
        return start_services
    if name == "AgentRuntime":
        from .runtime import AgentRuntime
        return AgentRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
