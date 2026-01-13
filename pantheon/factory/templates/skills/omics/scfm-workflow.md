---
id: scfm-workflow
name: Single-Cell Foundation Model Workflow
description: Single-cell foundation model workflow guidance (profile → validate → run → interpret)
section: workflows
tags: [scfm, single-cell, foundation-models, bioinformatics]
type: user
---

# Single-Cell Foundation Model Workflow

## When to Use This Skill

Use when the user wants to:
- Generate cell embeddings from scRNA-seq data
- Integrate batches of single-cell data
- Annotate cell types (future: requires fine-tuning)

## Model Selection (Use Capability, Not Name)

Always use `scfm_select_model()` instead of hardcoding model names:

```python
# DO: Let the system select based on data + task
selection = scfm_select_model(adata_path, task="embed")
selected_model = selection["recommended"]["name"]

# DON'T: Hardcode model names without checking compatibility
result = scfm_run(task="embed", model_name="uce", ...)  # Bad: ignores data compatibility
```

### Selection Criteria

| Data Characteristic | Recommended Model |
|---------------------|-------------------|
| Gene symbols (HGNC) | UCE, scGPT |
| Ensembl IDs (ENSG...) | Geneformer |
| Human only | Any model |
| Mouse | UCE, scGPT |
| Other species | UCE (multi-species) |

## Workflow Steps

### 1. Profile Data First

```python
profile = scfm_profile_data(adata_path)
# Returns: species, gene_scheme, modality, n_cells, n_genes
```

### 2. Validate Before Running

```python
validation = scfm_preprocess_validate(adata_path, model_name=selected_model, task="embed")
if validation["status"] != "ready":
    # Apply auto_fixes or inform user of issues
    ...
```

### 3. Run Model

```python
result = scfm_run(
    task="embed",
    model_name=selected_model,
    adata_path=adata_path,
    output_path=output_path,
)
```

### 4. Interpret Results

```python
qa = scfm_interpret_results(output_path, task="embed")
# Check: silhouette_score, visualizations
```

## Backend Selection

| Model | Min VRAM | CPU Fallback | Recommendation |
|-------|----------|--------------|----------------|
| UCE | 16 GB | No | Remote MCP if no GPU |
| scGPT | 8 GB | Yes | Local with fallback |
| Geneformer | 4 GB | Yes | Local preferred |

## Sanity Checks

After embedding, verify results:
1. **UMAP visualization** - Cells should cluster by type
2. **Silhouette score** - Higher is better (> 0.3 indicates structure)
3. **Batch mixing** - Check if batch effects are reduced (integration task)

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| "Gene scheme mismatch" | Wrong gene IDs | Convert symbols <-> Ensembl |
| "Species not supported" | Model limitation | Use UCE for exotic species |
| "GPU required" | No CUDA available | Use a CPU-capable model or remote MCP backend |
