# Cell2Sentence Adapter

## Status: 🔶 Conditional (Adapter Validated)

**Validation Results:**
- ✅ Adapter code validated (correct GPU detection, structured error messages)
- ✅ HuggingFace GPT-2 integration verified
- ⏳ Full inference requires GPU (8-16 GB VRAM)

## Overview
- **Paper:** [Cell2Sentence: Teaching Large Language Models the Language of Biology](https://www.biorxiv.org/content/10.1101/2023.09.11.557287v1)
- **GitHub:** [vandijklab/cell2sentence](https://github.com/vandijklab/cell2sentence)
- **Embedding Dim:** 768
- **Species:** human

## Architecture
Cell2Sentence converts single-cell expression profiles into text sequences by ranking genes by expression. These "cell sentences" are then embedded using a language model (GPT-2 by default).

## Checkpoint Setup

### HuggingFace (Automatic)
The adapter uses HuggingFace transformers for the language model:
```python
# No checkpoint_dir needed - uses HuggingFace models
result = await scfm_run(task="embed", model_name="cell2sentence", ...)
```

### Optional: Custom LLM
To use a different language model, set checkpoint_dir to the model path.

### Dependencies
```bash
pip install transformers scanpy torch
```

## Usage Example
```python
result = await scfm_run(
    task="embed",
    model_name="cell2sentence",
    adata_path="data.h5ad",
    output_path="output.h5ad",
)
```

## Input Contract
- **Gene ID scheme:** symbol (HGNC)
- **Species:** human
- **Modality:** RNA
- **Preprocessing:** Gene expression (will be ranked internally)

## Output Keys
- `obsm["X_cell2sentence"]`: Cell embeddings (768-dim, from GPT-2)
- `obs["cell2sentence_text"]`: Cell text representations (optional)

## Algorithm
1. For each cell: rank genes by expression (descending)
2. Create text: "GENE1 GENE2 GENE3 ..." (top N expressed genes)
3. Tokenize text with LLM tokenizer
4. Embed with LLM and mean-pool hidden states

## Known Limitations
- GPU required: Yes (8-16 GB VRAM)
- Human only
- Requires `transformers` package
- Embedding dimension depends on LLM choice (768 for GPT-2)

## Smoke Test
```bash
SCFM_RUN_HEAVY=1 pytest tests/test_scfm.py -k "test_cell2sentence_embed" -v
```
