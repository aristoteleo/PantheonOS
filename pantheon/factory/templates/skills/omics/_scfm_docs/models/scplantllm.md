# scPlantLLM Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ⏳ Requires local checkpoint + GPU (16+ GB VRAM)
- Note: Plant species only

## Overview
- **Paper:** [scPlantLLM: A Foundation Model for Plant Single-Cell Transcriptomics](https://www.biorxiv.org/content/10.1101/2024.08.12.607676v1)
- **GitHub:** [scPlantLLM/scPlantLLM](https://github.com/scPlantLLM/scPlantLLM)
- **Embedding Dim:** 512
- **Species:** plant (Arabidopsis, rice, maize, tomato, wheat)

## Architecture
scPlantLLM is a plant-specific foundation model designed to handle plant-specific challenges like polyploidy and large genomes.

## Checkpoint Setup

### Download
```bash
# Clone repository
git clone https://github.com/scPlantLLM/scPlantLLM
cd scPlantLLM

# Download pretrained weights
mkdir -p ~/.cache/scfm/scplantllm/
# Move checkpoint to standard location
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_SCPLANTLLM=~/.cache/scfm/scplantllm/
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
    model_name="scplantllm",
    adata_path="plant_data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** Plant gene IDs (varies by species)
- **Species:** plant, arabidopsis, rice, maize, tomato, wheat
- **Modality:** RNA
- **Preprocessing:** Standard plant RNA-seq preprocessing

## Supported Plant Species
| Species | Gene Pattern | Example |
|---------|-------------|---------|
| Arabidopsis | AT1G, AT2G, etc. | AT1G01010 |
| Rice | Os, LOC_Os | LOC_Os01g01010 |
| Maize | GRMZM, Zm | GRMZM2G000001 |
| Tomato | Solyc | Solyc01g005000 |
| Wheat | TraesCS | TraesCS1A01G000100 |

## Species Detection
The adapter auto-detects plant species from:
1. `adata.uns["species"]`
2. Gene name patterns in `adata.var_names`

## Input Dimensions
- **Features:** 5000 genes (to handle large plant genomes)
- Adapter selects top variable genes

## Output Keys
- `obsm["X_scplantllm"]`: Cell embeddings (512-dim)

## Known Limitations
- GPU required: Yes (16+ GB VRAM)
- Plant species only (not human/mouse)
- Handles polyploidy in gene naming

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_SCPLANTLLM=/path/to/scplantllm \
    pytest tests/test_scfm.py -k "test_scplantllm_embed" -v
```
