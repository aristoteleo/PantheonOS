# GeneCompass Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16-32 GB VRAM)

## Overview
- **Paper:** [GeneCompass: Deciphering Universal Gene Regulatory Mechanisms](https://doi.org/10.1101/2023.09.26.559542)
- **GitHub:** [xCompass-AI/GeneCompass](https://github.com/xCompass-AI/GeneCompass)
- **Embedding Dim:** 512
- **Species:** human, mouse

## Architecture
GeneCompass is pre-trained on ~120M cells, making it one of the largest-scale single-cell foundation models. It incorporates prior knowledge about gene regulatory networks and supports multi-species analysis.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/xCompass-AI/GeneCompass
cd GeneCompass

# Download pretrained weights (check repository for latest instructions)
# Weights are typically available via HuggingFace or direct download

mkdir -p ~/.cache/scfm/genecompass/
# Move checkpoint files to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_GENECOMPASS=~/.cache/scfm/genecompass/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install genecompass scanpy torch
# Or install from source
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="genecompass",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human, mouse
- **Modality:** RNA
- **Required preprocessing:** Gene expression (normalized or raw counts)

## Output Keys
- `obsm["X_genecompass"]`: Cell embeddings (512-dim)

## Known Limitations
- GPU required: Yes (16-32 GB VRAM recommended)
- Large model size due to extensive pretraining
- Supports human and mouse; other species require additional configuration

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_GENECOMPASS=/path/to/genecompass \
    pytest tests/test_scfm.py -k "test_genecompass_embed" -v
```
