# LangCell Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16 GB VRAM)

## Overview
- **Paper:** [LangCell: Language-Cell Pre-training for Cell Identity Understanding](https://arxiv.org/abs/2405.06708)
- **GitHub:** [PharMolix/LangCell](https://github.com/PharMolix/LangCell)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
LangCell uses a two-tower architecture that aligns cell embeddings with natural language text. It has:
- **Cell encoder:** MLP-based encoder for gene expression
- **Text encoder:** BERT-based encoder for text descriptions

This enables text-guided cell analysis and zero-shot cell type annotation.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/PharMolix/LangCell
cd LangCell

# Download pretrained weights
mkdir -p ~/.cache/scfm/langcell/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_LANGCELL=~/.cache/scfm/langcell/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install transformers scanpy torch
```

## Usage Example
```python
# Standard embedding
result = await scfm_run(
    task="embed",
    model_name="langcell",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)

# Text-guided embedding (optional)
result = await scfm_run(
    task="embed",
    model_name="langcell",
    adata_path="data.h5ad",
    output_path="output.h5ad",
    text_query="T cells with high CD4 expression",  # Optional
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Optional:** `text_query` for text-guided embeddings

## Output Keys
- `obsm["X_langcell"]`: Cell embeddings (512-dim)

## Known Limitations
- GPU required: Yes (16 GB VRAM)
- Human only
- Text encoder uses PubMedBERT or BERT
- Two-tower architecture increases model complexity

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_LANGCELL=/path/to/langcell \
    pytest tests/test_scfm.py -k "test_langcell_embed" -v
```
