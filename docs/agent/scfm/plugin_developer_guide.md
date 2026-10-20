# SCFM Plugin Developer Guide

Add your own single-cell foundation model to Pantheon — without modifying any Pantheon source code.

## Overview

The Pantheon SCFM (Single-Cell Foundation Model) plugin system allows third-party developers to register custom foundation models that are automatically discovered and made available through the standard `scfm_run`, `scfm_list_models`, and `scfm_select_model` tools.

A plugin provides two things:

1. A **ModelSpec** — declares the model's capabilities, I/O contract, and hardware requirements.
2. An **Adapter class** — implements the actual inference logic (subclass of `BaseAdapter`).

These are bundled together through a `register()` function. Pantheon discovers plugins via two mechanisms:

| Mechanism | Best for | How it works |
|-----------|----------|--------------|
| **Pip package** (entry points) | Distribution & production | Publish a Python package; Pantheon discovers it via the `pantheon.scfm` entry-point group |
| **Local plugin file** | Rapid development & testing | Drop a `.py` file in `~/.pantheon/plugins/scfm/`; Pantheon loads it at startup |

Both use the same `register()` contract.

Throughout this guide we use **SAM3** (Meta's Segment Anything Model 3) as a concrete example. SAM3 is a vision foundation model (848M parameters, DETR-based) that we wrap as a spatial transcriptomics cell segmentation adapter — it takes tissue images associated with spatial AnnData and produces cell segmentation masks stored back in the AnnData object.

---

## Quick Start: Local Plugin

The fastest way to get started. Create a single Python file and Pantheon will discover it automatically.

### Step 1: Create the plugin directory

```bash
mkdir -p ~/.pantheon/plugins/scfm
```

### Step 2: Write the plugin file

Create `~/.pantheon/plugins/scfm/sam3.py`:

```python
from pantheon.toolsets.scfm.registry import (
    ModelSpec,
    TaskType,
    Modality,
    GeneIDScheme,
    SkillReadyStatus,
    HardwareRequirements,
    OutputKeys,
)
from pantheon.toolsets.scfm.adapters.base import BaseAdapter


# 1. Define the model specification
SAM3_SPEC = ModelSpec(
    name="sam3",
    version="1.0.0",
    skill_ready=SkillReadyStatus.PARTIAL,
    tasks=[TaskType.SPATIAL],
    modalities=[Modality.SPATIAL],
    species=["human", "mouse"],          # works on any tissue image
    gene_id_scheme=GeneIDScheme.CUSTOM,  # vision model, not gene-based
    zero_shot_embedding=False,
    zero_shot_annotation=True,           # zero-shot segmentation
    output_keys=OutputKeys(annotation_key="sam3_cell_mask"),
    embedding_dim=256,                   # SAM3 image encoder output dim
    hardware=HardwareRequirements(
        gpu_required=True,
        min_vram_gb=8,
        recommended_vram_gb=16,
        cpu_fallback=False,
        default_batch_size=4,            # images are large
    ),
    differentiator="DETR-based vision model for zero-shot cell segmentation in tissue images",
    prefer_when="User needs cell segmentation from spatial transcriptomics tissue images",
    checkpoint_url="https://huggingface.co/facebook/sam3",
    paper_url="https://arxiv.org/abs/2511.16719",
    license_notes="SAM License",
)


# 2. Implement the adapter
class SAM3Adapter(BaseAdapter):
    def __init__(self, checkpoint_dir=None):
        super().__init__(SAM3_SPEC, checkpoint_dir)

    def run(self, task, adata_path, output_path, batch_key=None,
            label_key=None, device="auto", batch_size=4):
        import scanpy as sc
        import numpy as np

        device = self._resolve_device(device)
        adata = sc.read_h5ad(adata_path)
        self._preprocess(adata, task)
        self._load_model(device)

        # --- Run SAM3 segmentation on tissue images ---
        masks = self._segment_cells(adata, device)

        output_keys = self._postprocess(adata, masks, task)
        self._add_provenance(adata, task, output_keys)
        adata.write_h5ad(output_path)

        return {
            "status": "success",
            "output_path": output_path,
            "output_keys": output_keys,
            "stats": {
                "n_cells": adata.n_obs,
                "n_masks": int(masks.max()) if masks is not None else 0,
            },
        }

    def _load_model(self, device):
        pass  # Load SAM3 weights from self._resolve_checkpoint_dir()

    def _preprocess(self, adata, task):
        pass  # Validate spatial coordinates and tissue images exist

    def _segment_cells(self, adata, device):
        import numpy as np
        return np.zeros(adata.n_obs, dtype=np.int32)  # placeholder

    def _postprocess(self, adata, masks, task):
        key = self.spec.output_keys.annotation_key
        adata.obs[key] = masks
        return [f"obs['{key}']"]


# 3. The register() function — this is the plugin entry point
def register():
    return (SAM3_SPEC, SAM3Adapter)
```

### Step 3: Verify

```python
from pantheon.toolsets.scfm.registry import get_registry

registry = get_registry()

# Check that your model is discovered
spec = registry.get("sam3")
print(spec.name, spec.version)  # sam3 1.0.0

# Check that the adapter resolves
adapter_cls = registry.get_adapter_class("sam3")
print(adapter_cls.__name__)  # SAM3Adapter
```

---

## Quick Start: Pip Package

For distribution and production use, package your plugin as a pip-installable Python package.

### Package structure

```
pantheon-scfm-sam3/
├── pyproject.toml
└── src/
    └── pantheon_scfm_sam3/
        ├── __init__.py
        └── adapter.py
```

### `pyproject.toml`

```toml
[project]
name = "pantheon-scfm-sam3"
version = "1.0.0"
description = "SAM3 cell segmentation adapter for Pantheon SCFM"
requires-python = ">=3.10"
dependencies = [
    "pantheon-agents",
    "torch>=2.0",
    "segment-anything-3",
]

[project.entry-points."pantheon.scfm"]
sam3 = "pantheon_scfm_sam3:register"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

The key line is:

```toml
[project.entry-points."pantheon.scfm"]
sam3 = "pantheon_scfm_sam3:register"
```

This tells Pantheon to call `pantheon_scfm_sam3.register()` during plugin discovery.

### `src/pantheon_scfm_sam3/__init__.py`

```python
def register():
    from .adapter import SAM3_SPEC, SAM3Adapter
    return (SAM3_SPEC, SAM3Adapter)
```

### `src/pantheon_scfm_sam3/adapter.py`

Same adapter code as in the local plugin example above — define `SAM3_SPEC` and `SAM3Adapter`.

### Install and verify

```bash
pip install -e ./pantheon-scfm-sam3
python -c "from pantheon.toolsets.scfm.registry import get_registry; print(get_registry().get('sam3'))"
```

---

## API Reference

All types are imported from `pantheon.toolsets.scfm.registry` unless otherwise noted.

### Enums

#### `TaskType`

```python
class TaskType(str, Enum):
    EMBED = "embed"              # Generate cell embeddings
    ANNOTATE = "annotate"        # Cell type annotation
    INTEGRATE = "integrate"      # Batch integration / correction
    PERTURB = "perturb"          # Perturbation prediction
    SPATIAL = "spatial"          # Spatial transcriptomics analysis
    DRUG_RESPONSE = "drug_response"  # Drug response prediction
```

#### `Modality`

```python
class Modality(str, Enum):
    RNA = "RNA"
    ATAC = "ATAC"
    SPATIAL = "Spatial"
    PROTEIN = "Protein"
    MULTIOMICS = "Multi-omics"
```

#### `GeneIDScheme`

```python
class GeneIDScheme(str, Enum):
    SYMBOL = "symbol"    # HGNC gene symbols (e.g., TP53)
    ENSEMBL = "ensembl"  # Ensembl IDs (e.g., ENSG00000141510)
    CUSTOM = "custom"    # Model-specific gene set
```

#### `SkillReadyStatus`

```python
class SkillReadyStatus(str, Enum):
    READY = "ready"          # Full adapter spec, tested
    PARTIAL = "partial"      # Works with conditions
    REFERENCE = "reference"  # Reference docs only
```

### Dataclasses

#### `HardwareRequirements`

```python
@dataclass
class HardwareRequirements:
    gpu_required: bool = True          # GPU needed for inference?
    min_vram_gb: int = 8               # Minimum VRAM in GB
    recommended_vram_gb: int = 16      # Recommended VRAM in GB
    cpu_fallback: bool = False         # Can run on CPU?
    default_batch_size: int = 64       # Default batch size
```

#### `OutputKeys`

```python
@dataclass
class OutputKeys:
    embedding_key: str = ""      # adata.obsm key (e.g., "X_mymodel")
    annotation_key: str = ""     # adata.obs key (e.g., "mymodel_pred")
    confidence_key: str = ""     # adata.obs key (e.g., "mymodel_score")
    integration_key: str = ""    # adata.obsm key (e.g., "X_mymodel_integrated")
```

### `ModelSpec`

The complete specification for your foundation model.

```python
@dataclass
class ModelSpec:
    # Identity (required)
    name: str                  # Unique name, lowercase (e.g., "sam3")
    version: str               # Version string (e.g., "1.0.0")

    # Readiness
    skill_ready: SkillReadyStatus = SkillReadyStatus.REFERENCE

    # Capabilities
    tasks: list[TaskType] = []           # Supported tasks
    modalities: list[Modality] = []      # Supported data modalities
    species: list[str] = []              # e.g., ["human", "mouse"]

    # Input requirements
    gene_id_scheme: GeneIDScheme = GeneIDScheme.SYMBOL
    requires_finetuning: bool = False    # Some tasks need fine-tuning?
    zero_shot_embedding: bool = True     # Can embed without fine-tuning?
    zero_shot_annotation: bool = False   # Can annotate without fine-tuning?

    # Output contract
    output_keys: OutputKeys = OutputKeys()
    embedding_dim: int = 512             # Embedding vector dimension

    # Hardware
    hardware: HardwareRequirements = HardwareRequirements()

    # Routing hints (used by the model selector)
    differentiator: str = ""   # What makes this model unique
    prefer_when: str = ""      # When to specifically choose this model

    # Resources (informational)
    checkpoint_url: str = ""
    documentation_url: str = ""
    paper_url: str = ""
    license_notes: str = ""
```

### `BaseAdapter`

Import from `pantheon.toolsets.scfm.adapters.base`.

#### Constructor

```python
def __init__(self, spec: ModelSpec, checkpoint_dir: Optional[str] = None):
```

Sets `self.spec`, `self.checkpoint_dir`, `self._model`, `self._tokenizer`.

#### Abstract methods (you must implement)

```python
@abstractmethod
def run(
    self,
    task: TaskType,
    adata_path: str,
    output_path: str,
    batch_key: Optional[str] = None,
    label_key: Optional[str] = None,
    device: str = "auto",
    batch_size: int = 64,
) -> dict[str, Any]:
    """Execute the model. Return dict with output_path, output_keys, stats."""

@abstractmethod
def _load_model(self, device: str):
    """Load model weights and tokenizer from checkpoint."""

@abstractmethod
def _preprocess(self, adata, task: TaskType):
    """Prepare AnnData for model input."""

@abstractmethod
def _postprocess(self, adata, embeddings, task: TaskType) -> list[str]:
    """Write results to AnnData. Return list of output keys."""
```

#### Helper methods (provided by base class)

| Method | Description |
|--------|-------------|
| `_resolve_device(device)` | Resolves `"auto"` to `"cuda"`, `"mps"`, or `"cpu"` |
| `_resolve_checkpoint_dir(require=True)` | Finds checkpoint directory (see [Checkpoint Management](#checkpoint-management)) |
| `_find_checkpoint(path, extensions)` | Finds checkpoint file in a directory by extension |
| `_add_provenance(adata, task, output_keys)` | Records model run metadata in `adata.uns["scfm"]` |

---

## Complete Example

A full SAM3 adapter that wraps Meta's Segment Anything Model 3 for zero-shot cell segmentation in spatial transcriptomics tissue images:

```python
"""SAM3 cell segmentation adapter for Pantheon SCFM.

Wraps Meta's Segment Anything Model 3 (SAM3) as a spatial transcriptomics
adapter. Takes tissue images associated with an AnnData object and produces
cell segmentation masks stored in adata.obs.

References:
    - GitHub: https://github.com/facebookresearch/sam3
    - Paper:  https://arxiv.org/abs/2511.16719
"""

from pathlib import Path
from typing import Any, Optional

import numpy as np

from pantheon.toolsets.scfm.registry import (
    GeneIDScheme,
    HardwareRequirements,
    Modality,
    ModelSpec,
    OutputKeys,
    SkillReadyStatus,
    TaskType,
)
from pantheon.toolsets.scfm.adapters.base import BaseAdapter


SAM3_SPEC = ModelSpec(
    name="sam3",
    version="1.0.0",
    skill_ready=SkillReadyStatus.READY,
    tasks=[TaskType.SPATIAL],
    modalities=[Modality.SPATIAL],
    species=["human", "mouse"],           # works on any tissue image
    gene_id_scheme=GeneIDScheme.CUSTOM,   # vision model, not gene-based
    requires_finetuning=False,
    zero_shot_embedding=False,
    zero_shot_annotation=True,            # zero-shot segmentation
    output_keys=OutputKeys(
        annotation_key="sam3_cell_mask",
        confidence_key="sam3_score",
    ),
    embedding_dim=256,                    # SAM3 image encoder output dim
    hardware=HardwareRequirements(
        gpu_required=True,
        min_vram_gb=8,
        recommended_vram_gb=16,
        cpu_fallback=False,
        default_batch_size=4,             # tissue images are large
    ),
    differentiator="DETR-based vision foundation model for zero-shot cell segmentation",
    prefer_when="User needs automated cell segmentation from spatial transcriptomics tissue images",
    checkpoint_url="https://huggingface.co/facebook/sam3",
    documentation_url="https://github.com/facebookresearch/sam3",
    paper_url="https://arxiv.org/abs/2511.16719",
    license_notes="SAM License",
)


class SAM3Adapter(BaseAdapter):
    """Adapter for Meta's SAM3 model applied to cell segmentation.

    Expects spatial transcriptomics AnnData with:
      - adata.uns['spatial'][library_id]['images']['hires'] — tissue image
      - adata.obsm['spatial'] — cell spatial coordinates

    Produces:
      - adata.obs['sam3_cell_mask'] — integer mask ID per cell
      - adata.obs['sam3_score']     — segmentation confidence score
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        super().__init__(SAM3_SPEC, checkpoint_dir)

    def run(
        self,
        task: TaskType,
        adata_path: str,
        output_path: str,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: int = 4,
    ) -> dict[str, Any]:
        import scanpy as sc

        device = self._resolve_device(device)

        # Load spatial transcriptomics data
        adata = sc.read_h5ad(adata_path)
        self._preprocess(adata, task)

        # Load SAM3 model weights
        self._load_model(device)

        # Run cell segmentation on tissue images
        masks, scores = self._segment_tissue(adata, device, batch_size)

        # Write results back to AnnData
        output_keys = self._postprocess(adata, (masks, scores), task)
        self._add_provenance(adata, task, output_keys)
        adata.write_h5ad(output_path)

        n_unique_masks = len(np.unique(masks[masks > 0]))
        return {
            "status": "success",
            "output_path": output_path,
            "output_keys": output_keys,
            "stats": {
                "n_cells": adata.n_obs,
                "n_segments": n_unique_masks,
                "mean_confidence": float(np.mean(scores)),
                "device": device,
            },
        }

    def _load_model(self, device: str):
        if self._model is not None:
            return

        checkpoint_dir = self._resolve_checkpoint_dir()
        ckpt_path = self._find_checkpoint(
            Path(checkpoint_dir), extensions=[".pt", ".pth", ".safetensors"]
        )

        import torch
        from segment_anything_3 import SAM3Model  # SAM3 library

        self._model = SAM3Model.from_pretrained(ckpt_path)
        self._model.to(device)
        self._model.eval()

    def _preprocess(self, adata, task: TaskType):
        """Validate that spatial data and tissue images are present."""
        if "spatial" not in adata.obsm:
            raise ValueError(
                "AnnData missing spatial coordinates (adata.obsm['spatial']). "
                "SAM3 requires spatial transcriptomics data with tissue images."
            )

        spatial_data = adata.uns.get("spatial", {})
        if not spatial_data:
            raise ValueError(
                "AnnData missing tissue image data (adata.uns['spatial']). "
                "Provide a Visium or similar spatial dataset with associated images."
            )

    def _segment_tissue(
        self, adata, device: str, batch_size: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run SAM3 segmentation on tissue images.

        Returns:
            masks:  (n_obs,) int array — segment ID per cell
            scores: (n_obs,) float array — confidence per cell
        """
        import torch

        spatial_coords = adata.obsm["spatial"]  # (n_obs, 2)

        # Extract tissue image from adata.uns['spatial']
        library_id = list(adata.uns["spatial"].keys())[0]
        tissue_img = adata.uns["spatial"][library_id]["images"]["hires"]

        # Generate SAM3 image embeddings
        with torch.no_grad():
            img_tensor = torch.tensor(
                tissue_img.transpose(2, 0, 1),  # HWC -> CHW
                dtype=torch.float32,
            ).unsqueeze(0).to(device)

            image_embedding = self._model.image_encoder(img_tensor)

        # Use spatial coordinates as point prompts for SAM3
        masks = np.zeros(adata.n_obs, dtype=np.int32)
        scores = np.zeros(adata.n_obs, dtype=np.float32)

        with torch.no_grad():
            for start in range(0, adata.n_obs, batch_size):
                end = min(start + batch_size, adata.n_obs)
                point_coords = torch.tensor(
                    spatial_coords[start:end],
                    dtype=torch.float32,
                ).unsqueeze(1).to(device)  # (B, 1, 2)

                point_labels = torch.ones(
                    point_coords.shape[:2],
                    dtype=torch.int64,
                ).to(device)  # (B, 1)

                pred_masks, pred_scores, _ = self._model.predict(
                    image_embedding=image_embedding,
                    point_coords=point_coords,
                    point_labels=point_labels,
                )
                # Assign segment IDs (start+1 to avoid zero = background)
                masks[start:end] = np.arange(start + 1, end + 1)
                scores[start:end] = pred_scores[:, 0].cpu().numpy()

        return masks, scores

    def _postprocess(self, adata, results, task: TaskType) -> list[str]:
        masks, scores = results
        mask_key = self.spec.output_keys.annotation_key
        score_key = self.spec.output_keys.confidence_key

        adata.obs[mask_key] = masks
        adata.obs[score_key] = scores

        return [f"obs['{mask_key}']", f"obs['{score_key}']"]


def register():
    return (SAM3_SPEC, SAM3Adapter)
```

---

## Checkpoint Management

The `BaseAdapter` provides a built-in checkpoint resolution system. You do not need to implement your own.

### Resolution priority

When your adapter calls `self._resolve_checkpoint_dir()`, the base class checks these sources in order:

1. **Constructor parameter** — `checkpoint_dir` passed to `__init__`
2. **Model-specific env var** — `SCFM_CHECKPOINT_DIR_<MODEL_NAME_UPPER>` (e.g., `SCFM_CHECKPOINT_DIR_SAM3`)
3. **Base directory + subfolder** — `SCFM_CHECKPOINT_DIR` env var + `/<model_name>/` subfolder

### Directory layout convention

```
~/.cache/scfm/              # or wherever SCFM_CHECKPOINT_DIR points
├── sam3/
│   └── sam3_hiera_large.pt
├── scgpt/
│   └── whole-human-2024/
│       └── best_model.pt
└── geneformer/
    └── v2-106M/
```

### Finding checkpoint files

Use `self._find_checkpoint(path, extensions)` to locate a checkpoint file within a directory. It searches for files matching the extensions (e.g., `[".pt", ".pth", ".safetensors"]`) and prefers filenames containing "model", "checkpoint", or "best".

---

## Rules and Constraints

### Name protection

Built-in model names (scgpt, geneformer, uce, etc.) cannot be overridden by plugins. If your plugin uses a built-in name, a warning is logged and the registration is skipped. Choose a unique name.

### Conflict resolution

| Scenario | Behavior |
|----------|----------|
| Plugin tries to override a built-in | Rejected with warning |
| Two entry-point plugins register the same name | Last loaded wins (warning logged) |
| Local plugin and entry-point register the same name | Local wins (loaded after entry points) |

### Error handling

Plugin errors never crash Pantheon. All failures are logged as warnings and the offending plugin is skipped:

- `register()` raises an exception — skipped
- `register()` returns an unexpected type — skipped
- Adapter class does not subclass `BaseAdapter` — skipped
- Spec is not a `ModelSpec` instance — skipped
- Local plugin file has a syntax error — skipped
- Local plugin file has no `register()` function — skipped

### Constructor signature

Your adapter's `__init__` must accept a single optional parameter:

```python
def __init__(self, checkpoint_dir: Optional[str] = None):
```

This is because Pantheon instantiates adapters as `adapter_cls(checkpoint_dir)`.

---

## Testing Your Plugin

### 1. Verify discovery

```python
from pantheon.toolsets.scfm.registry import get_registry

registry = get_registry()

# Is the model registered?
spec = registry.get("sam3")
assert spec is not None
print(f"Found: {spec.name} v{spec.version}")

# Is the adapter class available?
cls = registry.get_adapter_class("sam3")
assert cls is not None
print(f"Adapter: {cls.__name__}")
```

### 2. Verify adapter instantiation

```python
adapter = cls("/path/to/sam3-checkpoints")
print(f"Adapter name: {adapter.name}")
print(f"Supported tasks: {[t.value for t in adapter.spec.tasks]}")
```

### 3. Verify listing

```python
# Your model should appear in the full model list
models = registry.list_models()
names = [m.name for m in models]
assert "sam3" in names
```

### 4. Run a smoke test (if you have test data)

```python
from pantheon.toolsets.scfm import SCFMToolSet

toolset = SCFMToolSet(name="test", checkpoint_dir="/path/to/checkpoints")
result = toolset.scfm_run(
    task="spatial",
    model_name="sam3",
    adata_path="visium_tissue.h5ad",
    output_path="sam3_output.h5ad",
    device="cuda",
)
print(result)
```

### 5. Write unit tests

```python
import pytest
from pantheon.toolsets.scfm.registry import get_registry


def test_sam3_registered():
    registry = get_registry()
    spec = registry.get("sam3")
    assert spec is not None
    assert spec.name == "sam3"
    assert "spatial" in [t.value for t in spec.tasks]


def test_sam3_adapter_resolves():
    registry = get_registry()
    cls = registry.get_adapter_class("sam3")
    assert cls is not None
    adapter = cls()  # No checkpoint dir — just test instantiation
    assert adapter.name == "sam3"
```

---

## Registering Multiple Models

A single `register()` function can return a list of `(ModelSpec, AdapterClass)` tuples:

```python
def register():
    return [
        (SAM3_SPEC, SAM3Adapter),
        (SAM3_LITE_SPEC, SAM3LiteAdapter),
    ]
```

This is useful when one package provides multiple model variants (e.g., SAM3 full vs. SAM3-Lite).

---

## FAQ

**Q: Do I need to modify any Pantheon source code?**
No. The plugin system is fully external. You only need to provide a `register()` function that returns `(ModelSpec, AdapterClass)`.

**Q: When are plugins discovered?**
Plugins are discovered when `ModelRegistry` is first initialized (typically on first call to `get_registry()`). This happens once per Python process.

**Q: Can I use the local plugin mechanism in production?**
Yes, but pip packages are recommended for production since they handle dependencies and versioning. Local plugins are best for development and testing.

**Q: My model has heavy dependencies (torch, transformers, etc.). Will they be imported at startup?**
No. The `register()` function is called during registry initialization, but your adapter class is only instantiated when the model is actually used. Put heavy imports inside your adapter methods (e.g., `_load_model`), not at module level.

**Q: Can I override a built-in model like scGPT?**
No. Built-in model names are protected. Choose a unique name for your model.

**Q: My model is a vision model, not a gene expression model. Can it still be a plugin?**
Yes. SAM3 is a good example — it uses `TaskType.SPATIAL`, `Modality.SPATIAL`, and `GeneIDScheme.CUSTOM`. The plugin system supports any model that can consume AnnData and write results back to it, regardless of the underlying modality.

**Q: What Python versions are supported?**
Python 3.10+ (matching Pantheon's requirement).
