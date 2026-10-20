"""Team module for Pantheon agents framework.

PantheonTeam runs a conversation's agents: in agent mode one agent with
sub-agent profiles it starts with call_agent, in team mode agents that
delegate to each other.
"""

from .base import Team
from .pantheon import PantheonTeam  # Chatroom and Repl use this


__all__ = [
    "Team",
    "PantheonTeam",
]
