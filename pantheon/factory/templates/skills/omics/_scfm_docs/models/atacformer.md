# Atacformer Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16+ GB VRAM)
- Note: ATAC-seq modality only (not RNA)

## Overview
- **Paper:** [Atacformer: A Foundation Model for Chromatin Accessibility](https://www.biorxiv.org/content/10.1101/2024.05.10.593439v1)
- **GitHub:** [Atacformer/Atacformer](https://github.com/Atacformer/Atacformer)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
Atacformer is a foundation model specifically designed for ATAC-seq chromatin accessibility data. It encodes peak accessibility patterns into cell embeddings.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/Atacformer/Atacformer
cd Atacformer

# Download pretrained weights
mkdir -p ~/.cache/scfm/atacformer/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_ATACFORMER=~/.cache/scfm/atacformer/
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
    model_name="atacformer",
    adata_path="atac_data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** custom (peak IDs)
- **Species:** human
- **Modality:** ATAC-seq
- **Input format:** Peak accessibility matrix in `adata.X`

## Expected AnnData Structure
```python
# adata.X: (n_cells, n_peaks) accessibility matrix
# adata.var_names: Peak IDs (e.g., "chr1:1000-2000")
```

## Input Dimensions
- **Peak features:** Up to 50,000 peaks
- Adapter selects top variable peaks if more are present

## Output Keys
- `obsm["X_atacformer"]`: Cell embeddings (512-dim)

## Modality Validation
The adapter validates that input is ATAC data:
- Checks for peak-style var_names
- Checks `adata.uns["modality"]` if present
- Rejects RNA data with error message

## Known Limitations
- GPU required: Yes (16+ GB VRAM)
- Human only
- ATAC-seq data only (not RNA)
- Large input dimension (50k peaks)

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_ATACFORMER=/path/to/atacformer \
    pytest tests/test_scfm.py -k "test_atacformer_embed" -v
```
