"""
Gene panel selection library.

Pure-Python helpers for the GPS workflow. Import what you need:

    from pantheon.toolsets.gene_panel import (
        GenePanelConfig,
        select_spapros,
        select_random_forest,
        select_scgenefit,
    )

Unlike other entries under ``pantheon.toolsets``, this package does
**not** register a :class:`ToolSet` with the agent runtime. The
selection algorithms run inside the agent's existing notebook / Python
sandbox — keeping the agent's toolset list minimal (see PR #48 review N7).
"""

from pantheon.toolsets.gene_panel.algorithms import (
    select_random_forest,
    select_scgenefit,
    select_spapros,
)
from pantheon.toolsets.gene_panel.config import GenePanelConfig

__all__ = [
    "GenePanelConfig",
    "select_spapros",
    "select_random_forest",
    "select_scgenefit",
]
