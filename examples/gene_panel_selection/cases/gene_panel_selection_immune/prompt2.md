# Task: Gene Panel Selection for Immune Oncology (1000 genes)

## Goal
Design a high-quality **1000-gene immune-oncology gene panel** for human tumor microenvironment (TME) profiling.
- **adata_path:** `/home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad`  
- **Dataset source:** bioRxiv Preprint (2024) — DOI: 10.1101/2024.01.17.576110  

## Biological purpose
The panel must allow comprehensive profiling of the human tumor microenvironment with the following capabilities:

1. **Resolve all major immune cell types**  
   - T cells, NK cells, B cells, plasma cells  
   - Myeloid lineages: macrophages, monocytes, dendritic cells, neutrophils  
   - Regulatory populations (Tregs, MDSCs, etc.)

2. **Characterize cancer signaling pathways**  
   - Oncogenes  
   - Tumor suppressors  
   - Cell-cycle states  
   - DNA damage & stress response  
   - Hypoxia, angiogenesis, EMT, proliferation markers

3. **Profile cytokine and chemokine states**  
   - IL, TNF, IFN families  
   - Exhaustion markers (PDCD1, LAG3, HAVCR2, TIGIT…)  
   - Activation, cytotoxicity, and inflammation signatures

4. **Determine cancer cell stages & heterogeneity**
   - Distinguish malignant vs. non-malignant cells  
   - Identify tumor subclones  
   - Capture signaling states (MAPK, PI3K, JAK-STAT, TGF-β, WNT)

5. **Enable cell-state analysis**
   - Exhaustion  
   - Activation  
   - Proliferation  
   - Senescence  
   - Stress programs

## Final expected output
A curated **1000-gene panel**, with:

- Full annotation for each gene  
- Genes grouped into meaningful major categories (immune, cytokine signaling, oncogenes, pathways, etc.)  
- Final panel optimized for:
  - cell-type separability  
  - immune profiling  
  - cancer pathway resolution  
  - interpretability  
  - deployment in spatial transcriptomics (Vizgen-style)


## Instructions for the leader agent
Follow the complete workflow:
- Understand existing results
- Validate computational environment
- Run dataset QC + Downsampling (selection_expert)
- Apply gene panel selection algorithms and compare the different panels (HVG, DE, SpaPROS, scGeneFit, Random Forest) (selection_expert)
- Perform biological curation to produce final curated 1000-gene panel (selection_expert)
- Perform biological interpretation of the results ( Bioliogist)
- Generate publication-quality PDF report (reporter)

The report should be very precise on the selection pipeline, well detailed and include : 
- all steps, methods performed and their description, curation logic 
- Figures and interpretations
- recap table using such structure for example for the curated gene panel:

   | Gene | Methods where it appears | Biological relevance (dataset context) | Relevance score |
   |------|--------------------------|-----------------------------------------|-----------------|
   
-A venn diagriam to see the intersection of the gene panels provided by each methods

Workdir: <WORKDIR PROVIDED BY team.run>
