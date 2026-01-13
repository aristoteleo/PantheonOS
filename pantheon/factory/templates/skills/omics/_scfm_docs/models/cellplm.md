# CellPLM Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ✅ CPU fallback supported (slower but works without GPU)
- ⏳ Requires local checkpoint (see setup below)

## Overview
- **Paper:** [CellPLM: Pre-training of Cell Language Model](https://doi.org/10.1101/2023.10.03.560734)
- **GitHub:** [OmicsML/CellPLM](https://github.com/OmicsML/CellPLM)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
CellPLM is a cell-centric foundation model that explicitly models cell–cell relationships. It features fast inference and is designed for transfer learning across different single-cell datasets.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/OmicsML/CellPLM
cd CellPLM

# Install package
pip install -e .

# Download pretrained weights
# Check repository for download instructions

mkdir -p ~/.cache/scfm/cellplm/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_CELLPLM=~/.cache/scfm/cellplm/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install cellplm scanpy torch
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="cellplm",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Required preprocessing:** Normalized expression

## Output Keys
- `obsm["X_cellplm"]`: Cell embeddings (512-dim)

## Known Limitations
- GPU required: Yes (8-16 GB VRAM)
- Requires `cellplm` package installation from source
- Models cell-cell relationships for improved representations

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_CELLPLM=/path/to/cellplm \
    pytest tests/test_scfm.py -k "test_cellplm_embed" -v
```
