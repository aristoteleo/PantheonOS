---
id: gene_panel_selection
name: Gene Panel Selection Workflow
description: |
  End-to-end workflow for gene panel design in scRNA-seq and spatial transcriptomics:
  dataset understanding + smart downsampling + train/test splits,
  algorithmic selection (HVG/DE/RF/scGeneFit/SpaPROS),
  optimal sub-panel discovery (ARI vs size), consensus scoring,
  biological completion with a stability gate (Completion Rule),
  and benchmarking on test splits (ARI/NMI/Silhouette + UMAP similarity).
tags: [gene-panel, selection, scrna-seq, spatial, scanpy, scverse, benchmarking, spapros, scgenefit, random-forest]
---

# Gene Panel Selection Workflow

This skill is used when you need to construct **biologically meaningful** and **algorithmically robust** gene panels.

## Workflow Enforcement (MANDATORY)

Determine which stage of the workflow (Steps 1–5) is required for the current task,
and **STRICTLY** follow the corresponding step(s).

Once a step is entered, all its mandatory sub-steps must be executed.
No partial execution or silent degradation is allowed.



## Workdir
Always work in the workdir provided by the leader agent.

## Calling other agents
You can call other agents by using the `call_agent(agent_name, instruction)` function.

- **Call the `browser_use` agent** for information collection:
  When you encounter software or biological knowledge you are not familiar with, call `browser_use` to search the web and collect the necessary information.

- **Call the `system_manager` agent** for software environment installation:
  When you need to install software packages, call `system_manager` to install them.

- **Call the `biologist` agent** for results interpretation:
  When you plot figures, compute a panel, or have intermediate results, call `biologist` to ask for interpretations and include them in your report.

## Visual understanding
Use the `observe_images` function in the `file_manager` toolset to examine images and figures.
If a figure is not publication-quality, replot it.

## Reporting
At the end of the task, write a markdown report named:

`report_analysis.md`

The report **must** include:
- Summary
- Data (inputs, key parameters, outputs)
- Results (figures + tables)
- Key findings
- Next steps

## Large datasets
If the dataset is large, perform **smart downsampling** while preserving **all cell types**.

---

# Workflow (IMPORTANT : STRICLY FOLLOW NEEDED STEPS)

## 1) Dataset Understanding and Splitting

Start with exploratory inspection using an **integrated notebook**.

### 1.1 Basic structure
Inspect:
- file format (h5ad or other)
- number of cells / genes
- batches / conditions
- `.obs`, `.var`, `.obsm`, `.uns`
- whether dataset has spatial or multimodal components

Checklist:
- [ ] Identify `label_key` (true cell type recommended if present)
- [ ] Identify batch/condition columns
- [ ] Confirm whether `adata.X` is raw counts or normalized/log1p

---

### 1.2 Downsampling (CRITICAL)
Rules:
- Downsample to **< 500k cells**, **preserving all cell types**
- If genes > **30000**, reduce to **<=30000** via QC/HVG for compute-heavy steps
- Save downsampled `adata` to a new file in workdir via `file_manager`

> [!IMPORTANT]
> Prefer stratified downsampling by `label_key` if available; otherwise stratify by clustering labels.

---

### 1.3 Splitting
If provided one dataset, split to preserve all cell type distribution across all datasets:
- 1 training dataset (diversified)
- several test batches (**at least 5**)
- constraint: each split **< 50k cells**
- make splits as non-redundant as possible and represent **all cell types**

---

### 1.4 Preprocessing status
Check:
- normalization
- PCA
- UMAP
- clustering

Recompute only if missing or invalid.

---

### 1.5 Preprocessing (if needed)
- QC (follow the QC skill if available)
- Normalize / log1p / scale
- PCA / neighbors / UMAP
- Batch correction (if needed)
- Leiden clustering
- DEG & marker detection
- Cell type annotation
- Marker plots (dotplots, heatmaps)

> [!IMPORTANT]
> If heavy steps are slow or unstable on notebook use python code

---

## 2) Algorithmic Gene Panel Selection (SEED STEP)

### 2.1 Pre-established methods
Algorithmic Methods = `{HVG, DE, Random Forest, scGeneFit, SpaPROS}`

- Use true cell type as `label_key` whenever available
- Implement HVG / DE via Scanpy
- Use GenePanelToolSet:
  - `select_scgenefit` (**Always use: max_constraints ≤ 1000**)
  - `select_spapros` (**Always use n_hvg lower than 3000**)
  - `select_random_forest`
- Always request **gene scores**
- Save each method score table to disk (CSV)

---

## 3) Optimal Sub-panel Discovery (Algorithmic)

For **each method independently**:

1. Rank genes by the method-specific **score CSV**
2. Create sub-panels `{100, 200, …, N}` by taking top-K genes
3. For each size:
   - Recompute Leiden clustering (over-clustering allowed)
   - Compute **ARI** between Leiden clustering and **true cell types**
4. Plot **ARI vs panel size**
5. Identify:
   - stable ARI plateau
   - consistently high performance

➡️ Best-performing method + size defines the **initial sub-panel (< N genes)**

**Note**: **SEED STEP** is performed using the training `adata`.

---

## 4) Consensus Scoring & Curation Logic (EXPLICIT)

### 4.1 Score normalization & consensus table
After all methods run:

1. **Normalize scores per method** so scoring is on the same scale (no method dominates)
2. Aggregate normalized scores into a **consensus table**
3. Rank all genes by **algorithmic consensus score**

Deliverable: a gene × {method score, normalized score, consensus score} table.

---

### 4.2 Curation pipeline (STRICT ORDER)

Final panel is built in **two phases**:

#### Phase 1 — Sub-panel (algorithmic)
- Use the optimal sub-panel identified in Step 3 as seed subpanel
- Do **not** change genes in the seed

#### Phase 2 — Completion (biological, consensus-driven)
Iterate until panel size = **N**.

0) **IMPORTANT: Completion Rule**
Before adding a set of genes:
- test whether it makes ARI drop considerably or become less stable (training)
- propose a panel that does **not** drop ARI even if its size is < N
- add a supplemental list to reach N **only if relevant to context**

**Panel size N is a target size. If biological completion degrades performance, propose**:
- an optimal stable panel (< N)
- a supplemental gene list to reach N if required  
Check this on the training dataset.

**Note**: Before biological lookup on supplemental genes, first inspect genes in the seed panel to see what biological coverage is already present, then complete.

1) Perform biological lookup with `browser_use` for genes relevant to the **leader-provided biological context** on:
   - GeneCards
   - GO
   - UniProt
   - Literature

2) If biologically relevant:
   - add gene until size **N**
   - ensure no redundancy
   - maintain balanced biological coverage
   - categorize every added gene into relevant biological categories (leader context, or inferred from dataset)
   - enforce the **Completion Rule** (no major drop in ARI / stability)

3) If still room:
   - fill remaining space with genes from the consensus table (by score priority), excluding genes already present

**Note**: Every accepted gene must be **justified**, assigned a **biological category**, and referenced with a source (seed/method score or website/literature) and a gene function if available.

---

## 5) Benchmarking (MANDATORY)

### 5.0 Panel genes comparison
Create an **UpSet plot** for all **N-size** algorithmic panels to see overlap.

Use the **full original dataset** for evaluation.

### 5.1 Dataset
Benchmarking is performed on **test datasets**.

### 5.2 Metrics
For each subset compute (across test splits):
1. all algorithmic **N** size panels
2. final curated **N** size panel
3. if curated **N** was not optimal per **Completion Rule**, also benchmark the optimal stable (<N) panel
4. full gene set baseline

Compute:
- Leiden over-clustering on panel genes
- **ARI, NMI** between Leiden and true labels
- **Silhouette Index** using Leiden assignments

Plots:
- one figure per metric
- boxplots
- high-quality formatting

### 5.3 UMAP comparison
Compute UMAPs for:
- full genes (reference)
- each algorithmic **N** size panel
- final curated **N** size panel
- if needed, the optimal stable panel

Compare vs reference:
- qualitative
- quantitative (distance correlation / Procrustes-like metrics)

---

## 6) Summarizing

Report must include the full workflow (Steps 1 → 5) and at minimum:

- **Objective & context**
- **Dataset description** (structure, labels, preprocessing status)
- **Algorithmic methods run** (HVG/DE/RF/scGeneFit/SpaPROS): what each optimizes (detailed)
- **Sub-panel selection**:
  - ARI vs size curves per method
  - UpSet plot of different panels (overlaps)
  - selection decision (method + size) and why
- **Consensus table construction**:
  - normalization choice
  - aggregation rule
  - resulting ranked list
- **Curation & completion reasoning (step-by-step)**:
  - per added gene: lookup → match to context → accept/reject
  - redundancy checks + category balance
  - **all biological references**
- **Benchmarking results**:
  - UpSet plot comparing algorithmic panels and curated panel
  - ARI/NMI/SI boxplots across test subsets
  - UMAP comparisons + quantitative similarity metric
  - interpretation of performance differences

### Tables (MANDATORY)
1) Recap table of final panel (all N genes):

| Gene | Methods where it appears | Biological Function | Relevance score |
|------|--------------------------|----------------------|-----------------|

2) Per-category count recap table based on leader context.

---

# Guidelines for integrated notebook usage

Use the `integrated_notebook` toolset to create/manage/execute notebooks.

- Keep all related code in the same notebook
- Each notebook handles one specific analysis task
- Start each notebook with a markdown cell:
  - background
  - objective
- After each code cell producing results, add a markdown cell explaining the result
- Save figures and also display them in notebook outputs

If memory becomes insufficient:
- close kernels using `manage_kernel`
- reduce compute via **stratified downsampling** (preserve all cell types) and/or split heavy operations into separate cells
- document decisions explicitly (what was checked, what was changed, why)

---

# Visualization quality gate

We expect **high-quality, publication-level figures**.

After generating a figure:
- inspect via `observe_images`
- if not good → replot

High-quality means:
- clear, readable
- labeled axes
- good color/contrast
- informative title (not too long)

If figure is not satisfactory → **replot**