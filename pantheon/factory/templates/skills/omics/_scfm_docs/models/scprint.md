# scPRINT Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (correct GPU detection, structured error messages)
- ✅ HuggingFace integration path verified
- ⏳ Full inference requires GPU (16 GB VRAM)

## Overview
- **Paper:** [scPRINT: pre-training on 50M cells allows robust gene network and cell type annotation](https://www.biorxiv.org/content/10.1101/2024.07.29.605556v1)
- **GitHub:** [cantinilab/scPRINT](https://github.com/cantinilab/scPRINT)
- **HuggingFace:** [cantinilab/scPRINT](https://huggingface.co/cantinilab/scPRINT)
- **Embedding Dim:** 512
- **Species:** human

## Architecture
scPRINT focuses on protein-coding genes and robust batch integration. It's pre-trained on ~50M cells and is available on HuggingFace for easy access.

## Checkpoint Setup

### Option 1: HuggingFace (Recommended)
The adapter automatically downloads from HuggingFace if no local checkpoint is specified:
```python
# No checkpoint_dir needed - will download from HuggingFace
result = await scfm_run(task="embed", model_name="scprint", ...)
```

### Option 2: Local Checkpoint
```bash
# Download from HuggingFace CLI
huggingface-cli download cantinilab/scPRINT --local-dir ~/.cache/scfm/scprint/

# Set environment variable
export SCFM_CHECKPOINT_DIR_SCPRINT=~/.cache/scfm/scprint/
```

### Dependencies
```bash
pip install scprint scanpy torch
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="scprint",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human only
- **Modality:** RNA
- **Gene filtering:** Protein-coding genes (adapter applies filtering)

## Output Keys
- `obsm["X_scprint"]`: Cell embeddings (512-dim)

## Known Limitations
- GPU required: Yes (16 GB VRAM recommended)
- Human only (no mouse support)
- Focuses on protein-coding genes

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 pytest tests/test_scfm.py -k "test_scprint_embed" -v
```
