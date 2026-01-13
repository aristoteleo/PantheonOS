# CellFM Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16+ GB VRAM)

## Overview
- **Paper:** [CellFM: a large-scale foundation model pre-trained on transcriptomics data](https://www.biorxiv.org/content/10.1101/2024.06.04.597369v1)
- **GitHub:** [CellFM/CellFM](https://github.com/CellFM/CellFM)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
CellFM uses an MLP/padding architecture (non-transformer) pre-trained on ~126M transcriptomics data points. It uses a deep encoder with padding-based gene selection.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/CellFM/CellFM
cd CellFM

# Download pretrained weights
mkdir -p ~/.cache/scfm/cellfm/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_CELLFM=~/.cache/scfm/cellfm/
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
    model_name="cellfm",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Input dimension:** Up to 20,000 genes (padding applied)

## Architecture Details
Deep MLP encoder:
- Input: 20,000 features (padded)
- Layers: 20000 → 2048 → 1024 → 512 → 512
- Activations: LayerNorm + GELU

## Output Keys
- `obsm["X_cellfm"]`: Cell embeddings (512-dim)

## Known Limitations
- GPU required: Yes (16+ GB VRAM)
- Human only
- Large input dimension (20k features)
- Non-transformer architecture

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_CELLFM=/path/to/cellfm \
    pytest tests/test_scfm.py -k "test_cellfm_embed" -v
```
