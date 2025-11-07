"""Team module for Pantheon agents framework.

This module provides different team patterns for coordinating multiple agents.
"""

from .base import Team
from .moa import MoATeam
from .pantheon import PantheonTeam
from .sequential import SequentialTeam
from .swarm import SwarmCenterTeam, SwarmTeam

__all__ = [
    "Team",
    "SwarmTeam",
    "SwarmCenterTeam",
    "PantheonTeam",
    "SequentialTeam",
    "MoATeam",
]
