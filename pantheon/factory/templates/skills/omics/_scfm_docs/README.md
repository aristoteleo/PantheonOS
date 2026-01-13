# Foundation Model Documentation

This directory contains documentation for the Single-Cell Foundation Model (SCFM) integration in Pantheon.

## Overview

The SCFM toolset provides a unified interface for 21+ foundation models trained on single-cell transcriptomics data. Each model has an adapter that translates the common API into model-specific inference calls.

## Directory Structure

```
_scfm_docs/
├── README.md                 # This file
├── SPEC_TEMPLATE.md          # Template for adding new model specs
├── checkpoint_layout.md      # Standard checkpoint organization
└── models/                   # Per-model documentation
    └── <model_name>.md
```

## Model Status Categories

| Status | Symbol | Meaning |
|--------|--------|---------|
| Working | ✅ | Real inference implemented and tested |
| Conditional | 🔶 | Works if package/checkpoint present |
| Scaffold | ⚠️ | Structure only, raises NotImplementedError |

## Current Status (21 Models)

- **Working (4):** UCE, scGPT, Geneformer, scFoundation
- **Conditional (17):** scBERT, GeneCompass, CellPLM, scPRINT, tGPT, Nicheformer, GenePT, Cell2Sentence, LangCell, scMulan, CellFM, scCello, AIDO.Cell, PULSAR, Atacformer, scPlantLLM, CHATCELL
- **Scaffold (0):** None (all models have real inference code paths; correctness still depends on real checkpoints/APIs for conditional models)

## Checkpoint Conventions

All model checkpoints follow a standard layout:

```
~/.cache/scfm/
├── scgpt/
│   └── whole-human-2024/
├── geneformer/
│   └── v2-106M/
├── uce/
│   └── 4-layer/
└── <model_name>/
    └── <version>/
```

See [checkpoint_layout.md](./checkpoint_layout.md) for detailed conventions.

## Adding a New Model

1. Create adapter in `pantheon/toolsets/scfm/adapters/<model>.py`
2. Register model in `pantheon/toolsets/scfm/registry.py`
3. Add documentation in `pantheon/factory/templates/skills/omics/_scfm_docs/models/<model>.md`
4. Update status matrix in `pantheon/factory/templates/skills/omics/scfm-models.md`

See [SPEC_TEMPLATE.md](./SPEC_TEMPLATE.md) for the model spec format.

## Quick Reference

### TaskTypes

| Task | Description | Output Key |
|------|-------------|------------|
| `embed` | Generate cell embeddings | `obsm['X_<model>']` |
| `integrate` | Batch correction | `obsm['X_<model>_integrated']` |
| `annotate` | Cell type prediction | `obs['<model>_celltype']` |
| `spatial` | Spatial analysis | `obsm['X_<model>_spatial']` |

### Common Parameters

```python
result = toolset.scfm_run(
    model_name="scgpt",           # Model to use
    task="embed",                 # Task type
    adata_path="data.h5ad",       # Input file
    output_path="output.h5ad",    # Output file
    device="auto",                # cuda/cpu/auto
    batch_size=32,                # Processing batch size
    batch_key="batch",            # Batch column for integration
    label_key="celltype",         # Label column for annotation
)
```

## Related Files

- **Toolset:** `pantheon/toolsets/scfm/toolset.py`
- **Registry:** `pantheon/toolsets/scfm/registry.py`
- **Adapters:** `pantheon/toolsets/scfm/adapters/`
- **Skill Doc:** `pantheon/factory/templates/skills/omics/scfm-models.md`
