"""Evolutionary algorithms written against `pantheon.evolution.core`.

Each module here is one algorithm and touches no framework code. That is the point: the test of
the abstraction is whether an algorithm nobody on this project designed can be added without
editing anything outside this directory.
"""
from .idea_code import IdeaCodeAlternating
from .map_elites import MapElitesIslands
from .simpletes import PUCTSelector, RPUCGSelector, Selector, SimpleTES

__all__ = [
    "MapElitesIslands",
    "IdeaCodeAlternating",
    "SimpleTES", "Selector", "PUCTSelector", "RPUCGSelector",
]
