# SCFM Real-World Data Test Plan (Stage A/B, sanitized)

This plan targets end-to-end validation on a remote GPU server using real `.h5ad` datasets (embeddings + provenance), and produces a reviewable results table.
It does **not** include any server IPs, usernames, ports, absolute paths, model weights, or data artifacts. Replace placeholders with your own paths.

---

## 0) Conventions & Outputs

### 0.1 Remote work directories (placeholders)

- Code: `<SCFM_WORKDIR>/pantheon-agents/`
- Data: `<SCFM_WORKDIR>/data/real/`
- Results: `<SCFM_WORKDIR>/runs/<DATE>_realworld_stageA/` and `<SCFM_WORKDIR>/runs/<DATE>_realworld_stageB/`

### 0.2 Resource constraints (recommended)

```bash
export CUDA_VISIBLE_DEVICES=0
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
```

---

## 1) Dataset Preparation (PBMC3k / Pancreas / Paul15)

All datasets are saved as `.h5ad` and a Stage A subset is generated (e.g., 500×3000).

### 1.1 PBMC3k (human, RNA)

- Source: `scanpy.datasets.pbmc3k()`
- Outputs:
  - `pbmc3k_symbol.h5ad`
  - `pbmc3k_symbol_500x3000.h5ad`
  - (optional) if `adata.var["gene_ids"]` exists, generate Ensembl variants for Geneformer:
    - `pbmc3k_ensembl.h5ad`
    - `pbmc3k_ensembl_500x3000.h5ad`

### 1.2 Pancreas (RNA; verify species first)

Some environments do not provide `scanpy.datasets.pancreas()`. Try in priority order:

1. `scanpy.datasets.pancreas()` (if available)
2. `scvelo.datasets.pancreas()` (if scvelo is installed)
3. Download/provide a pancreas `.h5ad` externally and place it under `<SCFM_WORKDIR>/data/real/`

Suggested outputs:
- `pancreas_symbol.h5ad`
- `pancreas_symbol_500x3000.h5ad`
- (optional) Ensembl variants: `pancreas_ensembl*.h5ad`

### 1.3 Paul15 (mouse, RNA; cross-species)

- Source: `scanpy.datasets.paul15()`
- Outputs:
  - `paul15_symbol.h5ad`
  - `paul15_symbol_500x3000.h5ad`

### 1.4 One-shot generator (example)

Run in the remote runner environment (requires `scanpy`; scvelo is optional):

```bash
cd <SCFM_WORKDIR>
python - <<'PY'
from __future__ import annotations

from pathlib import Path

import scanpy as sc

out = Path("<SCFM_WORKDIR>/data/real").expanduser()
out.mkdir(parents=True, exist_ok=True)

def write_dataset(name: str, adata, species: str | None = None):
    if species is not None:
        adata.uns["species"] = species

    symbol_path = out / f"{name}_symbol.h5ad"
    adata.write(symbol_path)

    sub = adata[: min(500, adata.n_obs), : min(3000, adata.n_vars)].copy()
    sub.write(out / f"{name}_symbol_500x3000.h5ad")

    if "gene_ids" in adata.var:
        ens = adata.copy()
        ens.var_names = ens.var["gene_ids"].astype(str)
        ens.var_names = [x.split(".")[0] for x in ens.var_names]
        if species is not None:
            ens.uns["species"] = species
        ens.write(out / f"{name}_ensembl.h5ad")
        ens_sub = ens[: min(500, ens.n_obs), : min(3000, ens.n_vars)].copy()
        ens_sub.write(out / f"{name}_ensembl_500x3000.h5ad")

pbmc = sc.datasets.pbmc3k()
write_dataset("pbmc3k", pbmc, species="human")

pancreas = None
if hasattr(sc.datasets, "pancreas"):
    pancreas = sc.datasets.pancreas()
else:
    try:
        import scvelo as scv
        pancreas = scv.datasets.pancreas()
    except Exception:
        pancreas = None

if pancreas is not None:
    write_dataset("pancreas", pancreas, species=None)  # profile before running human-only models
else:
    print("Pancreas dataset not available via scanpy/scvelo; please provide a pancreas .h5ad manually.")

paul15 = sc.datasets.paul15()
write_dataset("paul15", paul15, species="mouse")

print("Wrote datasets to:", out)
PY
```

---

## 2) Stage A: Subset End-to-End Validation (fast)

### 2.1 Inputs

- `pbmc3k_symbol_500x3000.h5ad`
- `paul15_symbol_500x3000.h5ad`
- (optional) `*_ensembl_500x3000.h5ad` (Geneformer only)
- (optional) `pancreas_symbol_500x3000.h5ad` (if prepared)

### 2.2 Model selection guidance

- Human/RNA: run as many models as possible excluding those requiring extra checkpoints / API keys / special inputs.
- Mouse/RNA (cross-species): prioritize models that declare mouse support in the registry (e.g., UCE/scGPT/GeneCompass/Nicheformer).

### 2.3 Pass criteria (per run)

- Output `.h5ad` is readable
- Expected embedding key is written (e.g., `obsm["X_<model>"]`) with correct shape
- Embedding contains no NaN/Inf
- Provenance metadata is writable (recommended: `uns["scfm"]["latest_json"]`, etc.)

---

## 3) Stage B: Full-scale Validation (only Stage A successes)

### 3.1 Inputs

- `pbmc3k_symbol.h5ad`
- `paul15_symbol.h5ad`
- (optional) `pancreas_symbol.h5ad`

### 3.2 Strategy

- Only re-run models that succeeded in Stage A.
- Optionally run QA on each output (UMAP/silhouette) to sanity-check embeddings.

---

## 4) Logging & Summary Artifacts (recommended)

After each stage, produce:
- `summary.json`: `{dataset, model, status, error, output_path}` rows
- Per-model directory: `result.json` + `output.h5ad`

This keeps review lightweight without requiring large log files.
