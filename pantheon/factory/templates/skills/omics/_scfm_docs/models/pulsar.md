# PULSAR Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16+ GB VRAM)

## Overview
- **Paper:** [PULSAR: A Foundation Model for Multi-scale and Multicellular Biology](https://www.biorxiv.org/content/10.1101/2025.01.02.630896v1)
- **GitHub:** [PULSAR-FM/PULSAR](https://github.com/PULSAR-FM/PULSAR)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
PULSAR is a multi-scale foundation model that captures cell-cell interactions and tissue-level patterns. It's designed for multicellular biology applications.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/PULSAR-FM/PULSAR
cd PULSAR

# Download pretrained weights
mkdir -p ~/.cache/scfm/pulsar/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_PULSAR=~/.cache/scfm/pulsar/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install scanpy torch
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="pulsar",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Multi-scale:** Supports tissue-level context when available

## Output Keys
- `obsm["X_pulsar"]`: Cell embeddings (512-dim)

## Multi-scale Features
PULSAR captures patterns at multiple scales:
- Cell-level gene expression
- Cell-cell interactions
- Tissue-level organization

## Known Limitations
- GPU required: Yes (16+ GB VRAM)
- Human only
- Multi-scale features require appropriate data structure

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_PULSAR=/path/to/pulsar \
    pytest tests/test_scfm.py -k "test_pulsar_embed" -v
```
