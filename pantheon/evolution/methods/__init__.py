"""Evolutionary algorithms written against `pantheon.evolution.core`.

Each module here is one algorithm and touches no framework code. That is the point: the test of
the abstraction is whether an algorithm nobody on this project designed can be added without
editing anything outside this directory.
"""
from .annealed_idea_code import AnnealedIdeaCode
from .idea_code import IdeaCodeAlternating
from .niche_menu import NicheMenu

MapElitesIslands = NicheMenu
"""The old name, kept so existing scripts and notebooks keep importing. The class
was renamed because the grid here is a parent-selection menu, not an archive --
see the module docstring of `niche_menu`."""
from .simpletes import PUCTSelector, RPUCGSelector, Selector, SimpleTES

__all__ = [
    "MapElitesIslands",
    "NicheMenu",
    "IdeaCodeAlternating",
    "AnnealedIdeaCode",
    "SimpleTES", "Selector", "PUCTSelector", "RPUCGSelector",
]
