"""Evolutionary algorithms written against `pantheon.evolution.core`.

Each module here is one algorithm and touches no framework code. That is the point: the test of
the abstraction is whether an algorithm nobody on this project designed can be added without
editing anything outside this directory.
"""
from .annealed_idea_code import AnnealedIdeaCode
from .idea_code import IdeaCodeAlternating
from .agent_map_elites import AgentMapElites

MapElitesIslands = AgentMapElites
NicheMenu = AgentMapElites
"""Earlier names, kept so existing scripts and notebooks keep importing. See the
module docstring of `agent_map_elites` for what each rename corrected."""
from .hypothesis_bandit import HypothesisBandit

PantheonEvo = HypothesisBandit
"""The working name during development; runs recorded under `pantheon_evo` load fine."""
from .simpletes import PUCTSelector, RPUCGSelector, Selector, SimpleTES
from .lab_notebook import LabNotebook
from .molt import Molt
from .molt2 import Molt2
from .molt2_factorial import Molt2Factorial
from .molt2_discovery import Molt2Discovery
from .molt2_decision import Molt2Decision

__all__ = [
    "AgentMapElites",
    "MapElitesIslands",
    "NicheMenu",
    "IdeaCodeAlternating",
    "AnnealedIdeaCode",
    "HypothesisBandit",
    "PantheonEvo",
    "SimpleTES", "Selector", "PUCTSelector", "RPUCGSelector",
    "LabNotebook",
    "Molt", "Molt2", "Molt2Factorial", "Molt2Discovery", "Molt2Decision",
]
