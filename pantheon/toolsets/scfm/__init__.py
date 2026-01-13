"""
Single-Cell Foundation Model (SCFM) Toolset

Provides unified, model-agnostic API for single-cell foundation models:
- scGPT, Geneformer, UCE, scFoundation, and more

Core tools:
- scfm_list_models() - Discover available models and capabilities
- scfm_describe_model() - Get full I/O contract for a model
- scfm_profile_data() - Auto-detect species, gene scheme, modality
- scfm_select_model() - Choose best model for task + data
- scfm_preprocess_validate() - Check compatibility, suggest fixes
- scfm_run() - Execute model (embed, annotate, integrate)
- scfm_interpret_results() - QA metrics and visualizations
"""

from .registry import (
    ModelRegistry,
    ModelSpec,
    TaskType,
    Modality,
    GeneIDScheme,
    get_registry,
)
from .toolset import SCFMToolSet

__all__ = [
    "SCFMToolSet",
    "ModelRegistry",
    "ModelSpec",
    "TaskType",
    "Modality",
    "GeneIDScheme",
    "get_registry",
]
