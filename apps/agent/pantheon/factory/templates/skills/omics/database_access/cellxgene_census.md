---
id: cellxgene_census_access
name: "CZ CELLxGENE Census: Single-Cell Data Access"
description: |
  Cloud-based Python API for accessing 217M+ single-cell RNA-seq observations
  from CZ CELLxGENE Discover via TileDB-SOMA. Supports flexible cell/gene
  metadata queries, streaming for larger-than-memory data, and pre-computed
  embeddings (scVI, Geneformer).
tags:
  - CELLxGENE
  - Census
  - single-cell
  - RNA-seq
  - TileDB-SOMA
  - AnnData
  - embeddings
  - scVI
  - Geneformer
---

# CZ CELLxGENE Census: Single-Cell Data Access

**Data size**: 217M+ total cells (125M+ unique) | **Organisms**: Human,
Mouse, Macaque, Marmoset, Chimpanzee

## Overview

CZ CELLxGENE Census provides cloud-based access to the largest curated
collection of single-cell RNA-seq data. Built on TileDB-SOMA, it enables
low-latency querying of larger-than-memory datasets with flexible metadata
filters. Data is returned as AnnData objects ready for analysis.

### Key Capabilities

- Query cells by tissue, cell type, disease, sex, assay, and more
- Filter genes by Ensembl ID or gene name
- Stream data in chunks for larger-than-memory processing
- Access pre-computed embeddings (scVI, Geneformer)
- Download raw or normalized count matrices

---

## Installation

**Requirements**: Linux or macOS, Python 3.10–3.12, >16 GB RAM, >5 Mbps
internet

```bash
pip install -U cellxgene-census

# With experimental features (embeddings, HVG)
pip install -U cellxgene-census[experimental]
```

> [!TIP]
> For best performance, use an AWS EC2 instance in `us-west-2` (data is
> hosted in that S3 region).

---

## Quick Start

```python
import cellxgene_census

# Open Census (use as context manager)
with cellxgene_census.open_soma() as census:
    # Get AnnData for specific cells and genes
    adata = cellxgene_census.get_anndata(
        census,
        organism="Homo sapiens",
        obs_value_filter="tissue == 'lung' and cell_type == 'B cell'",
        var_value_filter="feature_name in ['ACE2', 'TMPRSS2']",
        column_names={
            "obs": ["cell_type", "tissue", "disease", "sex", "assay"],
        },
    )
    print(adata)
```

---

## Core API

### open_soma — Open Census Connection

```python
cellxgene_census.open_soma(
    census_version="stable",  # "stable", "latest", or date like "2023-12-15"
    mirror=None,              # e.g., "s3-us-west-2"
    uri=None,                 # Direct S3 or local path
    tiledb_config=None,       # TileDB config overrides
)
```

Always use as a context manager:

```python
with cellxgene_census.open_soma() as census:
    # ... your queries here
    pass
```

**Version options**:
- `"stable"` — Latest long-term support (LTS) release (recommended)
- `"latest"` — Most recent weekly build
- `"2023-12-15"` — Specific dated release

---

### get_anndata — Query Count Matrices

```python
cellxgene_census.get_anndata(
    census,
    organism,                     # "Homo sapiens" or "Mus musculus"
    measurement_name="RNA",
    X_name="raw",                 # "raw" or "normalized"
    obs_value_filter=None,        # Cell metadata filter (SOMA syntax)
    var_value_filter=None,        # Gene metadata filter
    obs_coords=None,              # soma_joinid coordinates
    var_coords=None,              # soma_joinid coordinates
    column_names=None,            # {"obs": [...], "var": [...]}
    obs_embeddings=(),            # e.g., ["scvi", "geneformer"]
    obs_column_names=None,        # Shorthand for obs columns
    var_column_names=None,        # Shorthand for var columns
)
```

**Returns**: `anndata.AnnData`

#### Examples

```python
with cellxgene_census.open_soma() as census:
    # Basic query with filters
    adata = cellxgene_census.get_anndata(
        census,
        organism="Homo sapiens",
        var_value_filter="feature_id in ['ENSG00000161798', 'ENSG00000188229']",
        obs_value_filter="sex == 'female' and cell_type in ['microglial cell', 'neuron']",
        column_names={
            "obs": ["assay", "cell_type", "tissue", "disease"],
        },
    )

    # With embeddings
    adata = cellxgene_census.get_anndata(
        census,
        organism="Homo sapiens",
        obs_value_filter="tissue == 'tongue'",
        obs_embeddings=["scvi", "geneformer"],
    )
    # Access: adata.obsm["scvi"], adata.obsm["geneformer"]

    # Mouse data for specific tissue
    adata = cellxgene_census.get_anndata(
        census,
        organism="Mus musculus",
        obs_value_filter="tissue_general in ['brain', 'lung']",
    )

    # Specific dataset
    adata = cellxgene_census.get_anndata(
        census,
        organism="Mus musculus",
        obs_value_filter="dataset_id == '0bd1a1de-3aee-40e0-b2ec-86c7a30c7149'",
    )
```

---

### get_obs / get_var — Metadata Only

```python
# Cell metadata
cellxgene_census.get_obs(
    census, organism,
    value_filter=None,          # SOMA filter string
    column_names=None,          # Columns to return
)

# Gene metadata
cellxgene_census.get_var(
    census, organism,
    value_filter=None,
    column_names=None,
)
```

**Returns**: `pandas.DataFrame`

```python
with cellxgene_census.open_soma() as census:
    # All female microglial cells
    obs_df = cellxgene_census.get_obs(
        census, "Homo sapiens",
        value_filter="sex == 'female' and cell_type == 'microglial cell'",
        column_names=["cell_type", "tissue", "disease"],
    )

    # Gene metadata
    var_df = cellxgene_census.get_var(
        census, "Homo sapiens",
        value_filter="feature_name in ['ACE2', 'TMPRSS2']",
        column_names=["feature_name", "feature_length"],
    )
```

---

### get_presence_matrix — Dataset × Gene Coverage

```python
cellxgene_census.get_presence_matrix(census, organism, measurement_name="RNA")
```

**Returns**: `scipy.sparse.csr_array` of shape `[n_datasets, n_var]`

```python
with cellxgene_census.open_soma() as census:
    pm = cellxgene_census.get_presence_matrix(census, "Homo sapiens")
    # Check if a gene is present in a specific dataset
    is_present = pm[dataset_joinid, var_joinid][0, 0]
```

---

### download_source_h5ad — Download Original Datasets

```python
cellxgene_census.download_source_h5ad(
    dataset_id,
    to_path,
    census_version="stable",
)
```

```python
cellxgene_census.download_source_h5ad(
    "8e47ed12-c658-4252-b126-381df8d52a3d",
    to_path="data.h5ad",
)
```

---

## Query Filter Syntax

The `value_filter` parameter uses SOMA query syntax:

**Operators**: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`
**Logic**: `and`, `or`

### Common Patterns

```python
# Single equality
"sex == 'female'"

# Multiple values
"cell_type in ['microglial cell', 'neuron']"

# Combined conditions
"sex == 'female' and cell_type in ['microglial cell', 'neuron']"

# Complex multi-field
"cell_type == 'B cell' and tissue_general == 'lung' and disease == 'COVID-19'"

# Primary data only (exclude duplicates)
"is_primary_data == True"

# Gene filtering by Ensembl ID
"feature_id in ['ENSG00000161798', 'ENSG00000188229']"

# Gene filtering by name
"feature_name in ['ACE2', 'TMPRSS2', 'FURIN']"

# Exclude normal samples
"disease != 'normal'"
```

---

## Data Schema

### Cell Metadata (`obs`) Key Columns

| Column | Description |
|--------|-------------|
| `soma_joinid` | Integer join index |
| `dataset_id` | Unique dataset identifier |
| `assay` | Sequencing assay (e.g., "10x 3' v3") |
| `cell_type` | Cell type annotation |
| `cell_type_ontology_term_id` | CL ontology term |
| `tissue` | Tissue name |
| `tissue_general` | High-level tissue category |
| `disease` | Disease status |
| `sex` | `"male"`, `"female"`, `"unknown"` |
| `development_stage` | Developmental stage |
| `donor_id` | Donor identifier |
| `is_primary_data` | True = original, False = duplicate |
| `suspension_type` | `"cell"`, `"nucleus"`, or `"na"` |
| `self_reported_ethnicity` | Ethnicity (human only) |
| `raw_sum` | Sum of raw counts |
| `nnz` | Number of non-zero values |

### Gene Metadata (`var`) Key Columns

| Column | Description |
|--------|-------------|
| `soma_joinid` | Integer join index |
| `feature_id` | Ensembl gene ID (e.g., `ENSG00000161798`) |
| `feature_name` | Gene symbol (e.g., `AQP1`) |
| `feature_length` | Gene length |
| `nnz` | Non-zero values across all cells |

---

## Memory-Efficient Patterns

### Streaming with ExperimentAxisQuery

For datasets too large to fit in memory, use the streaming API:

```python
import cellxgene_census
import tiledbsoma

with cellxgene_census.open_soma() as census:
    human = census["census_data"]["homo_sapiens"]
    with human.axis_query(
        measurement_name="RNA",
        obs_query=tiledbsoma.AxisQuery(
            value_filter="tissue == 'brain' and is_primary_data == True"
        ),
    ) as query:
        # Stream X matrix in chunks
        for arrow_tbl in query.X("raw").tables():
            # Process each chunk (PyArrow Table)
            # Columns: soma_dim_0 (obs joinid), soma_dim_1 (var joinid), soma_data (value)
            pass
```

### Incremental Statistics

```python
import numpy as np

with cellxgene_census.open_soma() as census:
    human = census["census_data"]["homo_sapiens"]
    with human.axis_query(
        measurement_name="RNA",
        obs_query=tiledbsoma.AxisQuery(
            value_filter="tissue == 'brain' and is_primary_data == True"
        ),
    ) as query:
        n_vars = len(query.var().concat().to_pandas())
        raw_sum = np.zeros(n_vars, dtype=np.float64)
        raw_n = np.zeros(n_vars, dtype=np.int64)

        for arrow_tbl in query.X("raw").tables():
            var_idx = arrow_tbl["soma_dim_1"].to_numpy()
            data = arrow_tbl["soma_data"].to_numpy()
            np.add.at(raw_n, var_idx, 1)
            np.add.at(raw_sum, var_idx, data)

        raw_mean = raw_sum / query.n_obs
```

---

## Embeddings

### Available Embeddings

| Model | Organisms | Description |
|-------|-----------|-------------|
| **scVI** | All | Variational inference embeddings |
| **Geneformer** | Human only | Transformer-based foundation model |

### Accessing Embeddings

```python
with cellxgene_census.open_soma(census_version="2023-12-15") as census:
    # Via get_anndata (simplest)
    adata = cellxgene_census.get_anndata(
        census,
        organism="Homo sapiens",
        obs_value_filter="tissue == 'tongue'",
        obs_embeddings=["scvi", "geneformer"],
    )
    scvi_emb = adata.obsm["scvi"]       # numpy array
    gf_emb = adata.obsm["geneformer"]   # numpy array
```

### Exploring Available Embeddings

```python
import cellxgene_census.experimental as exp

# List all embeddings for a Census version
all_emb = exp.get_all_available_embeddings("2023-12-15")

# Find Census versions with a specific embedding
versions = exp.get_all_census_versions_with_embedding("scvi", "homo_sapiens")
```

---

## Exploring Census Metadata

### Census Summary

```python
with cellxgene_census.open_soma() as census:
    # Build summary (total cells, schema versions)
    summary = census["census_info"]["summary"].read().concat().to_pandas()

    # All included datasets
    datasets = census["census_info"]["datasets"].read().concat().to_pandas()

    # Pre-computed cell counts by category
    counts = census["census_info"]["summary_cell_counts"].read().concat().to_pandas()

    # Human cell type counts
    human_ct = counts[
        (counts.organism == "Homo sapiens") &
        (counts.category == "cell_type")
    ].sort_values("unique_cell_count", ascending=False)
```

### Generating Citations

```python
with cellxgene_census.open_soma() as census:
    datasets = census["census_info"]["datasets"].read().concat().to_pandas()

    # Get cells of interest
    obs_df = cellxgene_census.get_obs(
        census, "Homo sapiens",
        value_filter="tissue == 'cardiac atrium'",
        column_names=["dataset_id", "cell_type"],
    )

    # Find citations for used datasets
    used_datasets = datasets[datasets["dataset_id"].isin(obs_df["dataset_id"])]
    for _, row in used_datasets.iterrows():
        print(row["citation"])
```

---

## Census Versions

```python
import cellxgene_census

# List all available versions
versions = cellxgene_census.get_census_version_directory()

# LTS versions only (5-year guarantee)
lts_versions = cellxgene_census.get_census_version_directory(lts=True)

# Get details for a specific version
desc = cellxgene_census.get_census_version_description("2023-12-15")
```

**Release schedule**:
- **LTS releases**: Semi-annual, accessible for 5 years
- **Weekly releases**: Available without permanence guarantees

---

## Handling Duplicate Cells

Census may contain the same cells across multiple datasets. Use
`is_primary_data` to filter:

```python
# Always filter for unique cells in analyses
adata = cellxgene_census.get_anndata(
    census,
    organism="Homo sapiens",
    obs_value_filter="cell_type == 'B cell' and is_primary_data == True",
)
```

---

## Best Practices

1. **Always use context manager** (`with` statement) for `open_soma()`
2. **Filter `is_primary_data == True`** to avoid duplicate cells in analyses
3. **Specify `column_names`** to reduce memory usage — only fetch needed columns
4. **Use `var_value_filter`** to limit genes when you only need a few
5. **Pin `census_version`** for reproducibility (use dated version, not `"stable"`)
6. **Stream large queries** using `ExperimentAxisQuery` instead of `get_anndata`
7. **Use embeddings** when available — saves compute time vs. re-embedding
