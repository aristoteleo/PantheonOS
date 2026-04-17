"""
Gene panel selection configuration.

Provides :class:`GenePanelConfig` — a dataclass holding hyperparameters
for the selection algorithms and the surrounding workflow (downsampling
limits, ARI degradation threshold, etc.).

Values are loaded from ``settings.json`` via :meth:`from_settings`, with
sensible defaults baked in so the toolset is usable even when the user
has no project-level settings file.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from pantheon.utils.log import logger


@dataclass
class GenePanelConfig:
    """Hyperparameters for gene panel selection.

    Defaults reflect a good trade-off between precision and tractable
    compute. Override per-project via ``settings.json`` under the
    ``gene_panel`` section, or per-call by passing explicit kwargs to
    the selection tool methods.
    """

    # --- Algorithm caps (keep LP / RF tractable) ---
    scgenefit_max_constraints: int = 1000
    """Upper bound on LP constraints fed to scGeneFit."""

    spapros_n_hvg: int = 3000
    """HVG pre-filter size before running SpaPROS."""

    rf_n_estimators: int = 300
    """Number of trees for the Random Forest ranker."""

    # --- Workflow thresholds (consumed by the agent via the skill file) ---
    ari_drop_threshold: float = 0.05
    """Max acceptable ARI degradation during panel completion."""

    downsample_max_cells: int = 500_000
    """Above this cell count, downsampling is required before selection."""

    gene_count_threshold: int = 30_000
    """Above this gene count, gene subsetting is required before selection."""

    split_cell_limit: int = 50_000
    """Target cells per test split (soft cap, preserve diversity)."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GenePanelConfig":
        """Build from a raw dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_settings(cls) -> "GenePanelConfig":
        """Load from Pantheon settings (``gene_panel`` section).

        Falls back to defaults if settings cannot be loaded — useful when
        the toolset is imported outside an active Pantheon project.
        """
        try:
            from pantheon.settings import get_settings

            return cls.from_dict(get_settings().get_gene_panel_config())
        except Exception as e:  # pragma: no cover - best-effort fallback
            logger.debug(
                f"GenePanelConfig: falling back to defaults ({type(e).__name__}: {e})"
            )
            return cls()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


__all__ = ["GenePanelConfig"]
