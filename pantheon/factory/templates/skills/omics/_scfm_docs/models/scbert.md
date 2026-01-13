# scBERT Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ✅ CPU fallback supported (slower but works without GPU)
- ⏳ Requires local checkpoint (see setup below)

## Overview
- **Paper:** [scBERT as a Large-scale Pretrained Deep Language Model for Cell Type Annotation](https://doi.org/10.1038/s42256-022-00534-z)
- **GitHub:** [TencentAILabHealthcare/scBERT](https://github.com/TencentAILabHealthcare/scBERT)
- **Embedding Dim:** 200
- **Species:** human

## Architecture
scBERT uses a Performer architecture (linear attention) that enables full-genome attention without gene filtering. It's particularly robust for cell type annotation, especially for rare cell types.

## Checkpoint Setup

### Download
```bash
# Clone repository and download weights
git clone https://github.com/TencentAILabHealthcare/scBERT
cd scBERT
# Download pretrained weights from the repository

# Move checkpoint to standard location
mkdir -p ~/.cache/scfm/scbert/
cp panglao_pretrain.pth ~/.cache/scfm/scbert/
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_SCBERT=~/.cache/scfm/scbert/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Dependencies
```bash
pip install performer-pytorch scanpy torch
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="scbert",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Required preprocessing:** Raw counts or normalized expression

## Output Keys
- `obsm["X_scBERT"]`: Cell embeddings (200-dim)

## Known Limitations
- GPU recommended: Yes (min 8 GB VRAM; 16 GB recommended). CPU fallback is supported but slow.
- Requires `performer-pytorch` package
- Annotation requires fine-tuning (Pantheon v1 exposes `embed`/`integrate`)

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_SCBERT=/path/to/scbert \
    pytest tests/test_scfm.py -k "test_scbert_embed" -v
```
