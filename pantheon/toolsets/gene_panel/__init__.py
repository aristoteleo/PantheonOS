"""
Gene panel selection library.

Pure-Python helpers for the GPS workflow. Import what you need:

    from pantheon.toolsets.gene_panel import (
        GenePanelConfig,
        select_spapros,
        select_random_forest,
        select_scgenefit,
    )

Unlike the other entries under ``pantheon.toolsets``, this package does
**not** register a :class:`ToolSet` with the agent runtime. The
selection algorithms run inside the agent's existing notebook / python
sandbox — keeping the agent's toolset list minimal (see PR #48 review).
"""

from .algorithms import (
    estimate_spapros_runtime,
    select_random_forest,
    select_scgenefit,
    select_spapros,
)
from .config import GenePanelConfig

__all__ = [
    "GenePanelConfig",
    "select_spapros",
    "select_random_forest",
    "select_scgenefit",
    "estimate_spapros_runtime",
]
