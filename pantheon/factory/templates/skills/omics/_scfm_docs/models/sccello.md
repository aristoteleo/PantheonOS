# scCello Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16 GB VRAM)

## Overview
- **Paper:** [scCello: Cell-type Coherent Embedding for Single-cell Transcriptomics](https://www.biorxiv.org/content/10.1101/2024.02.21.581389v1)
- **GitHub:** [scCello/scCello](https://github.com/scCello/scCello)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
scCello is an ontology-aligned encoder optimized for cell-type coherence. It includes an annotation classification head for zero-shot cell type annotation.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/scCello/scCello
cd scCello

# Download pretrained weights
mkdir -p ~/.cache/scfm/sccello/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_SCCELLO=~/.cache/scfm/sccello/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install scanpy torch
```

## Usage Example
```python
# Embedding only
result = await scfm_run(
    task="embed",
    model_name="sccello",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)

# With annotation (zero-shot)
result = await scfm_run(
    task="annotate",
    model_name="sccello",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Tasks:** embed, annotate (zero-shot)

## Output Keys
- `obsm["X_sccello"]`: Cell embeddings (512-dim)
- `obs["sccello_pred"]`: Predicted cell types (for annotate task)

## Annotation
- Zero-shot annotation via classification head
- ~100 cell type classes
- Ontology-aligned predictions

## Known Limitations
- GPU required: Yes (16 GB VRAM)
- Human only
- Annotation limited to pre-defined ontology classes

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_SCCELLO=/path/to/sccello \
    pytest tests/test_scfm.py -k "test_sccello_embed" -v
```
