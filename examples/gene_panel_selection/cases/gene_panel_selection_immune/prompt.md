# Task: Gene Panel Selection for Immune Oncology (1000 genes)

## Goal
Design a high-quality **1000-gene immune-oncology gene panel** for human tumor microenvironment (TME) profiling.

- **adata_path:** `/home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad`    
- **Dataset source:** bioRxiv Preprint (2024) — DOI: 10.1101/2024.01.17.576110  

---

## Biological purpose
The panel must support comprehensive profiling of the human tumor microenvironment with the following capabilities:

### 1. Resolve all cell types  
- First, construct two **initial 1000-gene panels** using **SpaPROS** and **scGeneFit**, based on the cell-type labels in the dataset.  
- For each method, compute **Leiden clustering** on gene subsets of varying sizes (e.g. increasing subset sizes) and evaluate clustering quality with **ARI** against the true cell-type labels.  
- Use the resulting **ARI vs. panel size curves** to **quantitatively determine the optimal number of marker genes** required for robust cell-type resolution.  
- Keep the genes corresponding to this optimal size in the final panel.  
- We expect a **high ARI** for the chosen size.

For the next goals (2–5), use:
- The **dataset context**,  
- The **existing results** from SpaPROS and scGeneFit,  
- And **web-based biological knowledge**  
to **complete the panel to 1000 genes**, choosing **how many genes to allocate to each category** in a principled way.

### 2. Characterize cancer signaling pathways  
Include genes capturing:  
- Oncogenes  
- Tumor suppressors  
- Cell-cycle states  
- DNA damage & stress response  
- Hypoxia and angiogenesis  
- EMT and proliferation markers  

### 3. Profile cytokine and chemokine states  
Include genes capturing:  
- IL, TNF, IFN families  
- Exhaustion markers (e.g. PDCD1, LAG3, HAVCR2, TIGIT, …)  
- Activation, cytotoxicity, and inflammation signatures  

### 4. Determine cancer cell stages & heterogeneity
Include genes that allow you to:  
- Distinguish malignant vs. non-malignant cells  
- Identify tumor subclones  
- Capture signaling states (MAPK, PI3K, JAK–STAT, TGF-β, WNT, etc.)

### 5. Enable cell-state analysis
Ensure the panel can resolve key cell-state programs:  
- Exhaustion  
- Activation  
- Proliferation  
- Senescence  
- Stress-response programs  

---

## Final expected output
A curated **1000-gene panel**, with:

- **Full annotation** for each gene  
- Genes **grouped into meaningful major categories** (cell-type separability, immune lineages, cytokine signaling, oncogenes, pathways, etc.)  
- Final panel optimized for:
  - cell-type separability  
  - immune profiling  
  - cancer pathway resolution  
  - interpretability  
  - deployment in **spatial transcriptomics (Vizgen-style)**

---

## Instructions for the leader agent

Orchestrate the following complete workflow:

### 1. Cell-type separability (selection_expert)  
- Run gene panel selection with **SpaPROS** and **scGeneFit** using the cell-type labels.  
- For each method, build **ARI vs. panel size** curves (Leiden clustering vs. true cell types).  
- Based on these curves, determine:
  - (a) The **optimal number of genes** to allocate to cell-type separability.  
  - (b) The **final set of marker genes** for cell-type resolution to be included in the 1000-gene panel.  

### 2. Biology-driven completion of the panel (selection_expert)  
- Starting from the optimal cell-type marker set, complete the panel up to **1000 genes** by adding biologically relevant genes for objectives (2), (3), (4), and (5), using:
  - The dataset (`/home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad`)  
  - Web-based knowledge (pathways, canonical markers, literature, etc.)  
- Decide how many genes to allocate to each functional block (pathways, cytokines/chemokines, heterogeneity, state programs) and **justify this allocation**.

### 3. Biological interpretation (biologist)  
- Provide a **clear biological interpretation** of:
  - The optimal number of marker genes for cell-type resolution  
  - The coverage of immune lineages, cytokine/chemokine signaling, and cancer pathways  
  - How the final panel supports analysis of exhaustion, activation, proliferation, senescence, and stress programs in the TME  

### 4. Benchmarking of the final panel (selection_expert / benchmarking_expert)
Using the same dataset (`/home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad`):

1. **Dataset splitting**
   - Split the dataset into **5 non-redundant subsets** that:
     - Cover **all major cell types**  
     - Avoid strong redundancy between subsets (each subset should add information).  

2. **Metrics across gene sets**
   - For each subset, compute **Leiden clustering** and evaluate:
     - **ARI**, **NMI**, and **Silhouette Index (SI)**  
   - Do this for the following **4 gene sets**:
     1. 1000-gene SpaPROS panel  
     2. 1000-gene scGeneFit panel  
     3. 1000-gene final Vizgen-style panel (the panel computed in this workflow)  
     4. Full gene set of the dataset  
   - Plot **boxplots** of ARI, NMI, and SI across the 5 subsets for these 4 gene sets.

3. **UMAP comparison**
   - For each subset, compute UMAPs using:
     - The **full gene set** (reference)  
     - The 3 panels: SpaPROS-1000, scGeneFit-1000, and the final 1000-gene panel.  
   - Compare the UMAPs:
     - **Qualitatively** (visual similarity of global structure, separation of key populations, etc.)  
     - **Quantitatively**, using one or more **UMAP similarity / alignment metrics** (e.g. correlation of pairwise distances, Procrustes-like similarity, or other suitable quantitative measures) between the panel-based UMAPs and the full-gene UMAP.

Summarize which panel best preserves:
- Global structure  
- Local neighborhood relationships  
- Separation of key biological populations  

### 5. Report generation (reporter)  
- Generate a **publication-quality PDF report** (with all figures) describing the full pipeline and results.

The report must be very precise about the **selection pipeline**, with:

- A detailed description of all steps and methods (SpaPROS, scGeneFit, ARI computation for cell separability, Leiden clustering, benchmarking protocol, etc.)  
- A clear explanation of the **completion logic** and the reasoning used to determine the best size for cell-type separability  
- Figures and their interpretations, including the **ARI vs. panel size** plots  
- A **recap table** of the final gene panel, for example:

   | Gene | Methods where it appears | Biological relevance (dataset context) | Relevance score |
   |------|--------------------------|----------------------------------------|-----------------|

- A **Venn diagram** showing the intersections between gene sets obtained by each method (SpaPROS, scGeneFit, and the final Vizgen-style panel, if applicable).  
- A dedicated **benchmarking section** describing:
  - The dataset splitting strategy  
  - ARI/NMI/SI boxplots across subsets and gene sets  
  - UMAP comparisons and **quantitative UMAP similarity** results  

---

**Workdir:** `<WORKDIR PROVIDED BY team.run>`
