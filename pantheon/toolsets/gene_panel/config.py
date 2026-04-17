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
    the selection functions.
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

    n_training_splits: int = 1
    """Number of training datasets to build during the train/test split."""

    n_test_splits: int = 5
    """Minimum number of test splits to build (more is fine)."""

    # --- SpaPROS runtime gating thresholds (used by estimate_spapros_runtime) ---
    spapros_runtime_warning_minutes: float = 5.0
    """Estimated minutes above which severity becomes ``"slow"``."""

    spapros_runtime_skip_minutes: float = 30.0
    """Estimated minutes above which severity becomes ``"very_slow"``."""

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
        the library is imported outside an active Pantheon project.
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
