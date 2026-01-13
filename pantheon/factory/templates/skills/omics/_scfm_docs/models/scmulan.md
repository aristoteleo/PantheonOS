# scMulan Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16+ GB VRAM)

## Overview
- **Paper:** [scMulan: a multitask generative pre-trained language model for single-cell analysis](https://www.biorxiv.org/content/10.1101/2024.01.25.577152v1)
- **GitHub:** [scMulan/scMulan](https://github.com/scMulan/scMulan)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
scMulan is a multi-omics foundation model supporting RNA, ATAC, and Protein modalities. It creates joint embeddings in a shared latent space.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/scMulan/scMulan
cd scMulan

# Download pretrained weights
mkdir -p ~/.cache/scfm/scmulan/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_SCMULAN=~/.cache/scfm/scmulan/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install scmulan scanpy torch
```

## Usage Example
```python
# RNA embedding
result = await scfm_run(
    task="embed",
    model_name="scmulan",
    adata_path="rna_data.h5ad",
    output_path="output.h5ad",
)

# Multi-omics (if adata has multiple modalities)
# Modalities detected from adata.uns["modality"] or data structure
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modalities:** RNA, ATAC, Protein
- **Modality detection:** Via `adata.uns["modality"]` or automatic

## Modality-Specific Input Dimensions
- **RNA:** 2000 genes (top variable)
- **ATAC:** 5000 peaks
- **Protein:** 200 features

## Output Keys
- `obsm["X_scmulan"]`: Cell embeddings (512-dim, joint space)

## Known Limitations
- GPU required: Yes (16+ GB VRAM)
- Human only
- Multi-omics requires properly formatted AnnData
- Modality-specific encoders for each data type

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_SCMULAN=/path/to/scmulan \
    pytest tests/test_scfm.py -k "test_scmulan_embed" -v
```
