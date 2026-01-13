# Model Specification Template

Use this template when adding a new foundation model to the SCFM registry.

## ModelSpec Structure

```python
from pantheon.toolsets.scfm.registry import (
    ModelSpec,
    ModelCapabilities,
    ModelCheckpoint,
    OutputKeys,
    SkillReadyStatus,
)

MODEL_SPEC = ModelSpec(
    name="model_name",           # Lowercase, no spaces
    version="1.0.0",             # Semantic version
    description="Brief description of the model",

    capabilities=ModelCapabilities(
        tasks=["embed", "integrate"],  # Supported TaskTypes
        species=["human", "mouse"],    # Supported species
        modalities=["rna"],            # rna, atac, protein, spatial
        max_genes=20000,               # Max input genes
        max_cells=100000,              # Max cells per batch
        embedding_dim=512,             # Output embedding dimension
    ),

    checkpoint=ModelCheckpoint(
        source="huggingface",          # huggingface, github, url, local
        repo_id="org/model-name",      # HuggingFace repo or GitHub
        filename="model.pt",           # Checkpoint filename
        size_gb=2.5,                   # Approximate size
        default_path="~/.cache/scfm/model_name/v1",
    ),

    output_keys=OutputKeys(
        embedding_key="X_model",
        integration_key="X_model_integrated",
        annotation_key="model_celltype",
        prediction_key="model_pred",
    ),

    requirements=["torch>=2.0", "custom-package"],
    paper_url="https://doi.org/...",
    github_url="https://github.com/...",
    skill_ready=SkillReadyStatus.PARTIAL,  # READY, PARTIAL, or REFERENCE
)
```

## Field Descriptions

### Core Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique model identifier (lowercase) |
| `version` | str | Yes | Semantic version string |
| `description` | str | Yes | One-line model description |

### ModelCapabilities

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tasks` | list | Required | Supported task types |
| `species` | list | Required | Supported species |
| `modalities` | list | ["rna"] | Data modalities |
| `max_genes` | int | None | Maximum input genes |
| `max_cells` | int | None | Maximum cells per run |
| `embedding_dim` | int | None | Output embedding size |

### ModelCheckpoint

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | str | Required | Download source type |
| `repo_id` | str | None | Repository identifier |
| `filename` | str | None | Checkpoint filename |
| `size_gb` | float | None | Approximate download size |
| `default_path` | str | None | Default local path |

### OutputKeys

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `embedding_key` | str | `X_<name>` | Key in `adata.obsm` for embeddings |
| `integration_key` | str | `X_<name>_integrated` | Key for batch-corrected |
| `annotation_key` | str | `<name>_celltype` | Key in `adata.obs` for labels |
| `prediction_key` | str | `<name>_pred` | Key for predictions |

### SkillReadyStatus

| Status | Meaning |
|--------|---------|
| `READY` | Full inference implemented, tested |
| `PARTIAL` | Works with conditions (package/checkpoint) |
| `REFERENCE` | Scaffold only, not functional |

## Adapter Implementation

Create adapter at `pantheon/toolsets/scfm/adapters/<model>.py`:

```python
from pathlib import Path
from typing import Any, Optional
import numpy as np

from ..registry import TaskType, get_registry
from .base import BaseAdapter


class ModelAdapter(BaseAdapter):
    """Adapter for Model Name."""

    def __init__(self, checkpoint_dir: Optional[str] = None):
        spec = get_registry().get("model_name")
        if spec is None:
            raise ValueError("Model not found in registry")
        super().__init__(spec, checkpoint_dir)
        self._model = None

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 32,
    ) -> dict[str, Any]:
        """Run model inference."""
        # Validate task
        supported_tasks = [TaskType.EMBED]  # List supported tasks
        if task not in supported_tasks:
            return {"error": f"Task '{task.value}' not supported"}

        # Load data
        import scanpy as sc
        adata = sc.read_h5ad(adata_path)

        # Validate species if needed
        species = self._detect_species(adata)
        if species not in ["human", "mouse"]:
            return {"error": f"Species '{species}' not supported"}

        # Preprocess
        processed = self._preprocess(adata, task)

        # Run inference
        embeddings = self._run_inference(processed, device, batch_size)

        # Postprocess and save
        output_keys = self._postprocess(adata, embeddings, task)
        self._add_provenance(adata, task, output_keys)
        adata.write(output_path)

        return {
            "status": "success",
            "output_path": output_path,
            "output_keys": output_keys,
            "stats": {"n_cells": adata.n_obs, "embedding_dim": embeddings.shape[1]},
        }

    def _preprocess(self, adata, task):
        """Model-specific preprocessing."""
        import scanpy as sc
        adata = adata.copy()
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
        return adata

    def _run_inference(self, adata, device: str, batch_size: int) -> np.ndarray:
        """Run model inference."""
        # Implementation here
        raise NotImplementedError("Inference not implemented")

    def _postprocess(self, adata, embeddings, task) -> list[str]:
        """Write results to AnnData."""
        key = self.spec.output_keys.embedding_key
        adata.obsm[key] = embeddings
        return [f"obsm['{key}']"]
```

## Registration

Add to `pantheon/toolsets/scfm/registry.py`:

```python
# In _register_all_models():
self.register(MODEL_SPEC)

# In adapters/__init__.py:
from .model import ModelAdapter
```

## Checklist

- [ ] Create ModelSpec with all fields
- [ ] Implement adapter class
- [ ] Register in registry.py
- [ ] Export from adapters/__init__.py
- [ ] Add tests
- [ ] Document in pantheon/factory/templates/skills/omics/_scfm_docs/models/
- [ ] Update status matrix
