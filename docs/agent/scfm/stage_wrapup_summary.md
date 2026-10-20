# SCFM Stage Wrap-up Summary (sanitized)

This document is intended for PR review and does **not** include any server IPs, usernames, ports, absolute paths, model weights, or data artifacts.

---

## 1) Objective

Validate the `pantheon-agents` SCFM (Single-Cell Foundation Model) toolset on a remote GPU server:
- Resolve multi-model dependency conflicts (e.g., torch/torchtext, transformers/huggingface_hub, NumPy ABI)
- Establish a reproducible execution strategy (per-model isolated environments)
- Complete end-to-end validation on real `.h5ad` data: input → embeddings → output `.h5ad` with provenance

---

## 2) Core Engineering Changes (high level)

### 2.1 Dependency isolation: per-model conda envs

- Create a dedicated conda env per model (naming convention: `scfm-<model>`) to isolate conflicting dependencies.
- Typical conflicts:
  - `scGPT` needs a torchtext-compatible stack
  - `tGPT` requires a newer torch due to security requirements

### 2.2 Execution strategy: conda subprocess

- Run inference via subprocess in the model env: `conda run -n scfm-<model> python ...`.
- The parent process remains responsible for:
  - data compatibility checks
  - parameter preparation
  - collecting and parsing JSON results from stdout

### 2.3 HDF5/AnnData compatibility: provenance serialization

- Fix `.h5ad` write failures caused by nested Python structures (e.g., `list[dict]`) in `adata.uns`.
- Store provenance as JSON strings (e.g., `latest_json` + `runs_json`) to keep HDF5-safe metadata.

### 2.4 UCE: model source and output alignment fixes

- Avoid blocked external downloads by switching UCE weights to a HuggingFace source (cache-friendly).
- Fix output path joining and cell filtering alignment:
  - UCE may drop cells without valid gene mappings (output cells < input cells)
  - The saved `.h5ad` uses UCE’s output AnnData and reports filtered counts

---

## 3) Real-data Validation (summary)

### 3.1 Stage A (small subset)

- Datasets: PBMC3k (human RNA) and Paul15 (mouse RNA), subset (e.g., 500 cells × 3000 genes).
- Outcome: models that do not require extra checkpoints completed end-to-end and wrote embeddings + provenance.

### 3.2 Stage B (full scale)

- Datasets: PBMC3k full and Paul15 full.
- Outcome: Stage A passing models also passed on full datasets with consistent output writing.

---

## 4) Current Model Status (by requirements)

- **Auto-download / no extra checkpoints (most RNA models)**: validated on real data (see internal logs).
- **Requires checkpoint directory**: `scGPT`, `Geneformer`, `scFoundation`
- **Requires API key**: `GenePT`
- **Requires special inputs**: `Atacformer` (ATAC), `scPlantLLM` (plant data)

---

## 5) Next Decisions

- Land the validated remote changes as reviewable patches in the repo (split into focused commits/PRs).
- Decide:
  - checkpoint staging conventions and env vars (`scGPT`/`Geneformer`/`scFoundation`)
  - whether to include `GenePT` in validation (compliance/cost)
  - the validation scope and data sources for ATAC/plant models
