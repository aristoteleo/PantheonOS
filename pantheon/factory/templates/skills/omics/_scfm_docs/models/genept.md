# GenePT Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (proper checkpoint resolution, error messages)
- ✅ CPU-only inference (no GPU required)
- ⏳ Requires pre-computed gene embeddings file

## Overview
- **Paper:** [GenePT: A Simple But Effective Foundation Model for Genes and Cells Built From ChatGPT](https://www.biorxiv.org/content/10.1101/2023.10.16.562533v1)
- **GitHub:** [yiqunchen/GenePT](https://github.com/yiqunchen/GenePT)
- **Embedding Dim:** 1536
- **Species:** human

## Architecture
GenePT uses pre-computed GPT-3.5 embeddings of gene descriptions from NCBI. Cell embeddings are computed as expression-weighted averages of gene embeddings, making it computationally efficient.

## Checkpoint Setup

### Download Pre-computed Gene Embeddings
```bash
# Clone repository
git clone https://github.com/yiqunchen/GenePT
cd GenePT

# Download pre-computed gene embeddings
# (typically gene_embeddings.npy or similar)

mkdir -p ~/.cache/scfm/genept/
cp embeddings/gene_embeddings.npy ~/.cache/scfm/genept/
cp embeddings/gene_names.txt ~/.cache/scfm/genept/
```

### Environment Variables
```bash
export SCFM_CHECKPOINT_DIR_GENEPT=~/.cache/scfm/genept/
# OR
export SCFM_CHECKPOINT_DIR=~/.cache/scfm/
```

### Expected Files in checkpoint_dir
```
genept/
├── gene_embeddings.npy  # (n_genes, 1536) array
├── gene_names.txt       # gene name mapping
└── (or gene_embeddings.pkl with dict mapping)
```

### Dependencies
```bash
pip install numpy scanpy
# No GPU required for inference
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="genept",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Gene matching:** Matches adata.var_names against pre-computed embeddings

## Output Keys
- `obsm["X_genept"]`: Cell embeddings (1536-dim)

## Algorithm
1. Load pre-computed gene embeddings (1536-dim per gene)
2. For each cell: compute weighted average of gene embeddings by expression
3. Formula: `cell_emb = sum(expr[g] * gene_emb[g]) / sum(expr)`

## Known Limitations
- GPU required: No (CPU inference)
- Human only
- Requires pre-computed gene embeddings file
- Gene coverage depends on embeddings file (typically ~18k genes)

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_GENEPT=/path/to/genept \
    pytest tests/test_scfm.py -k "test_genept_embed" -v
```
