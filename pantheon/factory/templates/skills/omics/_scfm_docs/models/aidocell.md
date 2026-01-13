# AIDO.Cell Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16 GB VRAM)

## Overview
- **Paper:** [AIDO.Cell: A Foundation Model for Single-Cell Analysis](https://www.biorxiv.org/content/10.1101/2024.04.01.587631v1)
- **GitHub:** [genbio-ai/AIDO.Cell](https://github.com/genbio-ai/AIDO.Cell)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
AIDO.Cell is a dense transformer optimized for zero-shot clustering. It uses LayerNorm-based architecture and is pre-trained on ~50M cells.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/genbio-ai/AIDO.Cell
cd AIDO.Cell

# Download pretrained weights
mkdir -p ~/.cache/scfm/aidocell/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_AIDOCELL=~/.cache/scfm/aidocell/
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
    model_name="aidocell",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Preprocessing:** Standard normalization

## Output Keys
- `obsm["X_aidocell"]`: Cell embeddings (512-dim)

## Clustering
AIDO.Cell embeddings are optimized for downstream clustering tasks. Use with standard clustering methods:
```python
import scanpy as sc
adata = sc.read("output.h5ad")
sc.pp.neighbors(adata, use_rep="X_aidocell")
sc.tl.leiden(adata)
```

## Known Limitations
- GPU required: Yes (16 GB VRAM)
- Human only
- Optimized for clustering, may not be best for other tasks

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_AIDOCELL=/path/to/aidocell \
    pytest tests/test_scfm.py -k "test_aidocell_embed" -v
```
