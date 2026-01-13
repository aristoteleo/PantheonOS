# CHATCELL Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16-32 GB VRAM)

## Overview
- **Paper:** [CHATCELL: A chat-based interface for single-cell data analysis](https://www.biorxiv.org/content/10.1101/2024.02.25.581960v1)
- **GitHub:** [chatcell/CHATCELL](https://github.com/chatcell/CHATCELL)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
CHATCELL is a chat-based interface for single-cell analysis. It combines a cell encoder with a text model for natural language interaction and zero-shot annotation.

## Components
- **Cell encoder:** MLP-based expression encoder (512-dim)
- **Annotation head:** Classification head (~100 cell types)
- **Text model:** Optional PubMedBERT/BERT for text-guided analysis

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/chatcell/CHATCELL
cd CHATCELL

# Download pretrained weights
mkdir -p ~/.cache/scfm/chatcell/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_CHATCELL=~/.cache/scfm/chatcell/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install transformers scanpy torch
```

## Usage Example
```python
# Embedding only
result = await scfm_run(
    task="embed",
    model_name="chatcell",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)

# With annotation
result = await scfm_run(
    task="annotate",
    model_name="chatcell",
    adata_path="data.h5ad",
    output_path="output.h5ad",
    query="What cell types are present?",  # Optional natural language query
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human only
- **Modality:** RNA
- **Tasks:** embed, annotate

## Output Keys
- `obsm["X_chatcell"]`: Cell embeddings (512-dim)
- `obs["chatcell_pred"]`: Predicted cell types (for annotate task)

## Annotation
- Zero-shot annotation via classification head
- Optional natural language query support
- ~100 pre-defined cell type classes

## Known Limitations
- GPU required: Yes (16-32 GB VRAM)
- Human only
- Text model requires `transformers` package
- Annotation limited to pre-defined classes

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_CHATCELL=/path/to/chatcell \
    pytest tests/test_scfm.py -k "test_chatcell_embed" -v
```
