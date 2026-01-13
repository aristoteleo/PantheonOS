# Nicheformer Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16+ GB VRAM)

## Overview
- **Paper:** [Nicheformer: a foundation model for single-cell and spatial transcriptomics](https://www.biorxiv.org/content/10.1101/2024.04.15.589472v1)
- **GitHub:** [theislab/nicheformer](https://github.com/theislab/nicheformer)
- **Embedding Dim:** 512
- **Species:** human, mouse

## Architecture
Nicheformer is a foundation model for spatial transcriptomics that predicts cellular niches and neighbors. It supports both standard scRNA-seq and spatial data with coordinate information.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/theislab/nicheformer
cd nicheformer

# Download pretrained weights (check repository for latest)
mkdir -p ~/.cache/scfm/nicheformer/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_NICHEFORMER=~/.cache/scfm/nicheformer/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install nicheformer scanpy torch pytorch-lightning
```

## Usage Example
```python
# Standard embedding
result = await scfm_run(
    task="embed",
    model_name="nicheformer",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)

# With spatial coordinates
# Ensure adata.obsm["spatial"] contains coordinate data
result = await scfm_run(
    task="spatial",
    model_name="nicheformer",
    adata_path="spatial_data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human, mouse
- **Modality:** RNA, Spatial
- **Optional:** `adata.obsm["spatial"]` for spatial coordinates

## Output Keys
- `obsm["X_nicheformer"]`: Cell embeddings (512-dim)
- For spatial task: additional niche/neighbor predictions

## Known Limitations
- GPU required: Yes (16+ GB VRAM)
- Spatial features require coordinate data
- Uses PyTorch Lightning for model loading

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_NICHEFORMER=/path/to/nicheformer \
    pytest tests/test_scfm.py -k "test_nicheformer_embed" -v
```
