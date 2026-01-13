---
id: scfm-models
name: Single-Cell Foundation Model Reference
description: Model reference cards (I/O contracts, gene ID schemes, hardware)
section: patterns
tags: [scfm, single-cell, foundation-models, reference]
type: user
---

# Single-Cell Foundation Model Reference

Quick reference for available models. Use `scfm_describe_model(name)` for full details.

---

## Implementation Status Matrix

This section clarifies which adapters have **working inference** vs **conditional inference** vs **scaffolds** (structure exists but raises `NotImplementedError`; currently none).

| Model | Status | Notes |
|-------|--------|-------|
| UCE | ✅ Working | Subprocess-based, multi-species support |
| scGPT | ✅ Working | Direct PyTorch inference, CPU fallback |
| Geneformer | ✅ Working | Two-stage tokenize→embed pipeline |
| scFoundation | ✅ Working | Direct API call |
| scBERT | 🔶 Conditional | Real inference code, needs `performer-pytorch` + checkpoint |
| GeneCompass | 🔶 Conditional | Real inference code, needs `genecompass` package + checkpoint |
| CellPLM | 🔶 Conditional | Real inference code, needs `cellplm` package + checkpoint |
| Nicheformer | 🔶 Conditional | Spatial transcriptomics focus, needs `nicheformer` package + checkpoint |
| scMulan | 🔶 Conditional | Multi-omics (RNA, ATAC, Protein), needs PyTorch + checkpoint |
| tGPT | 🔶 Conditional | Next-token prediction, uses HuggingFace `transformers` |
| CellFM | 🔶 Conditional | MLP/padding architecture, needs PyTorch + checkpoint |
| scCello | 🔶 Conditional | Ontology-aligned annotation, needs PyTorch + checkpoint |
| scPRINT | 🔶 Conditional | Protein-coding gene focus, needs `scprint` package + checkpoint |
| AIDO.Cell | 🔶 Conditional | Dense transformer for clustering, needs PyTorch + checkpoint |
| PULSAR | 🔶 Conditional | Multi-scale architecture, needs PyTorch + checkpoint |
| Atacformer | 🔶 Conditional | ATAC-seq peak encoder, needs PyTorch + checkpoint |
| scPlantLLM | 🔶 Conditional | Plant-specific encoder, needs PyTorch + checkpoint |
| LangCell | 🔶 Conditional | Two-tower architecture, needs `transformers` |
| Cell2Sentence | 🔶 Conditional | LLM bridge, needs `transformers` |
| GenePT | 🔶 Conditional | Pre-computed gene embeddings, needs checkpoint |
| CHATCELL | 🔶 Conditional | Chat-based annotation, needs PyTorch/transformers + checkpoint |

**Legend:**
- ✅ **Working**: Full inference implemented, tested, and functional
- 🔶 **Conditional**: Real inference code implemented, requires package installation and checkpoint download
- ⚠️ **Scaffold**: Adapter structure exists (ModelSpec, I/O contracts, tests) but `_run_*_direct()` raises `NotImplementedError`

**What "Conditional" means:**
- Real inference code is implemented (not `NotImplementedError`)
- Requires installing the model's specific package
- Requires downloading model checkpoint
- Will run actual inference when dependencies are met

**Note:** All 17 conditional models have been adapter-validated (correct error handling, checkpoint resolution). They remain 🔶 Conditional until full inference is tested with real checkpoints on GPU.

---

## Skill-Ready Models (✅ Working)

### UCE (Universal Cell Embeddings) ✅

**Best for:** Multi-species analysis, zero-shot embeddings

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human, mouse, zebrafish, macaque, pig, frog, lemur |
| Embedding dim | 1280 |
| GPU required | Yes (16 GB VRAM) |
| Output key | `obsm["X_uce"]` |

```python
result = scfm_run(task="embed", model_name="uce", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### scGPT ✅

**Best for:** Human/mouse data with gene symbols, flexible tasks

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human, mouse |
| Embedding dim | 512 |
| GPU required | Yes (8 GB), CPU fallback available |
| Output key | `obsm["X_scGPT"]` |

```python
result = scfm_run(task="embed", model_name="scgpt", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### Geneformer ✅

**Best for:** Human data with Ensembl gene IDs

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | **Ensembl (ENSG...)** - NOT symbols |
| Species | Human only |
| Embedding dim | 512 |
| GPU required | No (recommended), CPU fallback available |
| Output key | `obsm["X_geneformer"]` |

**Important:** Geneformer requires Ensembl gene IDs (e.g., `ENSG00000141510`), not gene symbols (e.g., `TP53`).

```python
result = scfm_run(task="embed", model_name="geneformer", adata_path="ensembl_data.h5ad", output_path="out.h5ad")
```

---

## Partial-Spec Models (`skill_ready=partial`)

### scFoundation (xTrimoGene) ✅

**Best for:** Full-transcriptome human RNA tasks

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | **Custom 19,264 gene set** |
| Species | Human only |
| Embedding dim | 512 |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_scfoundation"]` |

```python
result = scfm_run(task="embed", model_name="scfoundation", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### scBERT 🔶

**Best for:** Human data, rare cell type detection

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 200 |
| GPU required | Yes (8-16 GB), CPU fallback available |
| Architecture | Performer (full-genome attention) |
| Output key | `obsm["X_scBERT"]` |

```python
result = scfm_run(task="embed", model_name="scbert", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### GeneCompass 🔶

**Best for:** Large-scale analysis with prior knowledge

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human, mouse |
| Embedding dim | 512 |
| Training scale | 120M cells (largest) |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_genecompass"]` |

```python
result = scfm_run(task="embed", model_name="genecompass", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### CellPLM 🔶

**Best for:** Fast inference, large datasets

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| GPU required | Yes (8-16 GB), CPU fallback available |
| Architecture | Cell-centric (efficient) |
| Output key | `obsm["X_cellplm"]` |

```python
result = scfm_run(task="embed", model_name="cellplm", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### Nicheformer 🔶

**Best for:** Spatial transcriptomics

| Property | Value |
|----------|-------|
| Tasks | embed, integrate, spatial |
| Gene IDs | Symbol (HGNC) |
| Species | Human, mouse |
| Embedding dim | 512 |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Focus | Cell niche and spatial context |
| Output key | `obsm["X_nicheformer"]` |

```python
result = scfm_run(task="embed", model_name="nicheformer", adata_path="spatial_data.h5ad", output_path="out.h5ad")
```

---

### scMulan 🔶

**Best for:** Multi-omics integration (RNA + ATAC + Protein)

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Modalities | RNA, ATAC, Protein, Multi-omics |
| Embedding dim | 512 |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_scmulan"]` |

```python
result = scfm_run(task="embed", model_name="scmulan", adata_path="multiome_data.h5ad", output_path="out.h5ad")
```

---

## Model Comparison

| Feature | UCE | scGPT | Geneformer | scFoundation | scBERT | GeneCompass | CellPLM | Nicheformer | scMulan |
|---------|-----|-------|------------|--------------|--------|-------------|---------|-------------|---------|
| Gene IDs | Symbol | Symbol | Ensembl | Custom | Symbol | Symbol | Symbol | Symbol | Symbol |
| Species | Multi | H/M | Human | Human | Human | H/M | Human | H/M | Human |
| Embed dim | 1280 | 512 | 512 | 512 | 200 | 512 | 512 | 512 | 512 |
| Min VRAM | 16 GB | 8 GB | 4 GB | 16 GB | 8 GB | 16 GB | 8 GB | 16 GB | 16 GB |
| CPU support | No | Yes | Yes | No | Yes | No | Yes | No | No |
| Spatial | No | No | No | No | No | No | No | Yes | No |
| Multi-omics | No | No | No | No | No | No | No | No | Yes |

## Quick Decision Tree

```
What is your primary use case?
├── Multi-species analysis → UCE
├── Spatial transcriptomics → Nicheformer
├── Multi-omics (RNA+ATAC+Protein) → scMulan
├── Standard scRNA-seq:
│   ├── What gene ID format?
│   │   ├── Ensembl (ENSG...) → Geneformer
│   │   ├── Symbol (TP53...) → Continue below
│   │   └── Custom 19,264 gene set → scFoundation
│   └── What species?
│       ├── Human only:
│       │   ├── Large dataset, fast inference → CellPLM
│       │   ├── Rare cell detection → scBERT
│       │   └── General purpose → scGPT
│       ├── Human + Mouse → GeneCompass or scGPT
│       └── Other species → UCE
└── No GPU available:
    ├── Ensembl IDs → Geneformer
    └── Symbol IDs → scGPT or CellPLM
```

## Specialized & Emerging Models (2024-2025) 🔶 Conditional

### tGPT 🔶

**Best for:** Next-token prediction approach, capacity-focused architecture

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Training scale | ~57M cells |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_tgpt"]` |

```python
result = scfm_run(task="embed", model_name="tgpt", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### CellFM 🔶

**Best for:** Largest-scale analysis, MLP architecture

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Training scale | ~126M data points (largest) |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_cellfm"]` |

```python
result = scfm_run(task="embed", model_name="cellfm", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### scCello 🔶

**Best for:** Zero-shot cell type annotation, ontology alignment

| Property | Value |
|----------|-------|
| Tasks | embed, integrate, **annotate** |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Zero-shot annotation | **Yes** |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_sccello"]`, `obs["sccello_pred"]` |

```python
result = scfm_run(task="annotate", model_name="sccello", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### scPRINT 🔶

**Best for:** Robust batch integration, protein-coding gene focus

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Training scale | ~22M cells |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_scprint"]` |

```python
result = scfm_run(task="embed", model_name="scprint", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### AIDO.Cell 🔶

**Best for:** Zero-shot clustering, dense transformer architecture

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Training scale | ~50M cells |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_aidocell"]` |

```python
result = scfm_run(task="embed", model_name="aidocell", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### PULSAR 🔶

**Best for:** Multi-scale analysis, multicellular biology

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Focus | Cell-cell interactions, tissue context |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_pulsar"]` |

```python
result = scfm_run(task="embed", model_name="pulsar", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### Atacformer 🔶

**Best for:** ATAC-seq chromatin accessibility data

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | **Peak-based** (not gene-based) |
| Species | Human only |
| Modality | **ATAC-seq** |
| Embedding dim | 512 |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_atacformer"]` |

**Important:** Atacformer is designed for ATAC-seq data with peak-based features, not RNA-seq.

```python
result = scfm_run(task="embed", model_name="atacformer", adata_path="atac_data.h5ad", output_path="out.h5ad")
```

---

### scPlantLLM 🔶

**Best for:** Plant single-cell data, polyploidy handling

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol |
| Species | **Plant only** (Arabidopsis, rice, maize, etc.) |
| Embedding dim | 512 |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_scplantllm"]` |

**Important:** scPlantLLM is specifically designed for plant single-cell data and will reject non-plant data.

```python
result = scfm_run(task="embed", model_name="scplantllm", adata_path="plant_data.h5ad", output_path="out.h5ad")
```

---

### LangCell 🔶

**Best for:** Text-guided analysis, interpretable embeddings

| Property | Value |
|----------|-------|
| Tasks | embed, integrate |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Architecture | Two-tower (cell + text) |
| Embedding dim | 512 |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_langcell"]` |

```python
result = scfm_run(task="embed", model_name="langcell", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### Cell2Sentence 🔶

**Best for:** LLM fine-tuning, text-based cell representation

| Property | Value |
|----------|-------|
| Tasks | embed |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 768 (LLM dimension) |
| Requires finetuning | **Yes** |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_cell2sentence"]` |

```python
result = scfm_run(task="embed", model_name="cell2sentence", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### GenePT 🔶

**Best for:** API-based analysis, GPT-3.5 gene embeddings

| Property | Value |
|----------|-------|
| Tasks | embed |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | **1536** (GPT-3.5) |
| GPU required | **No** (API-based) |
| Requirements | OpenAI API key |
| Output key | `obsm["X_genept"]` |

**Important:** GenePT requires an OpenAI API key. Set `OPENAI_API_KEY` environment variable.

```python
result = scfm_run(task="embed", model_name="genept", adata_path="data.h5ad", output_path="out.h5ad")
```

---

### CHATCELL 🔶

**Best for:** Natural language interaction, chat-based annotation

| Property | Value |
|----------|-------|
| Tasks | embed, **annotate** |
| Gene IDs | Symbol (HGNC) |
| Species | Human only |
| Embedding dim | 512 |
| Zero-shot annotation | **Yes** |
| GPU required | Yes (16-32 GB VRAM), No CPU fallback |
| Output key | `obsm["X_chatcell"]`, `obs["chatcell_pred"]` |

```python
result = scfm_run(task="annotate", model_name="chatcell", adata_path="data.h5ad", output_path="out.h5ad")
```

---

## Extended Model Comparison

| Feature | tGPT | CellFM | scCello | scPRINT | AIDO.Cell | PULSAR | Atacformer | scPlantLLM | LangCell | Cell2Sentence | GenePT | CHATCELL |
|---------|------|--------|---------|---------|-----------|--------|------------|------------|----------|---------------|--------|----------|
| Tasks | E,I | E,I | E,I,A | E,I | E,I | E,I | E,I | E,I | E,I | E | E | E,A |
| Gene IDs | Sym | Sym | Sym | Sym | Sym | Sym | Peak | Sym | Sym | Sym | Sym | Sym |
| Species | H | H | H | H | H | H | H | Plant | H | H | H | H |
| Dim | 512 | 512 | 512 | 512 | 512 | 512 | 512 | 512 | 512 | 768 | 1536 | 512 |
| GPU | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | No | Yes |
| Zero-shot | Yes | Yes | **Yes** | Yes | Yes | Yes | Yes | Yes | Yes | No | Yes | **Yes** |

*E=Embed, I=Integrate, A=Annotate, H=Human, Sym=Symbol*

## Extended Quick Decision Tree

```
What is your primary use case?
├── Multi-species analysis → UCE
├── Spatial transcriptomics → Nicheformer
├── Multi-omics (RNA+ATAC+Protein) → scMulan
├── ATAC-seq only → Atacformer
├── Plant data → scPlantLLM
├── Zero-shot cell type annotation:
│   ├── Ontology-aligned → scCello
│   └── Chat-based → CHATCELL
├── No GPU available:
│   ├── API-based (OpenAI) → GenePT
│   ├── Ensembl IDs → Geneformer
│   └── Symbol IDs → scGPT or CellPLM
├── Largest training scale:
│   ├── ~126M points → CellFM
│   └── ~120M cells → GeneCompass
├── Text-guided analysis → LangCell
└── Standard scRNA-seq:
    ├── Ensembl (ENSG...) → Geneformer
    └── Symbol (TP53...) → scGPT, scPRINT, AIDO.Cell, or tGPT
```
