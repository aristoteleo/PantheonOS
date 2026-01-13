# tGPT Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (correct GPU detection, structured error messages)
- ✅ HuggingFace integration path verified
- ⏳ Full inference requires GPU (8-16 GB VRAM)

## Overview
- **Paper:** [Transcriptome-GPT: Next-token prediction for single-cell transcriptomics](https://www.biorxiv.org/content/10.1101/2024.03.01.582678v1)
- **HuggingFace:** [lixiangchun/transcriptome-gpt-1024-8-16-64](https://huggingface.co/lixiangchun/transcriptome-gpt-1024-8-16-64)
- **Embedding Dim:** 768
- **Species:** human

## Architecture
tGPT (Transcriptome-GPT) uses a GPT-2 style architecture with next-token prediction. It converts gene expression profiles to ranked gene sequences, then embeds them using a transformer.

## Checkpoint Setup

### HuggingFace (Automatic)
The adapter automatically downloads from HuggingFace:
```python
# No checkpoint_dir needed
result = await scfm_run(task="embed", model_name="tgpt", ...)
```

### Manual Download
```bash
# Download model
huggingface-cli download lixiangchun/transcriptome-gpt-1024-8-16-64 \
    --local-dir ~/.cache/scfm/tgpt/

# Set environment variable (optional)
export SCFM_CHECKPOINT_DIR_TGPT=~/.cache/scfm/tgpt/
```

### Dependencies
```bash
pip install transformers scanpy torch
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="tgpt",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Preprocessing:** Gene expression converted to ranked gene sequences

## Output Keys
- `obsm["X_tgpt"]`: Cell embeddings (768-dim, GPT-2 hidden size)

## Known Limitations
- GPU required: Yes (8-16 GB VRAM)
- Human only
- Requires `transformers` package

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 pytest tests/test_scfm.py -k "test_tgpt_embed" -v
```
