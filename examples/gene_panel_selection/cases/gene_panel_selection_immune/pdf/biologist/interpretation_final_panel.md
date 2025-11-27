Immune-oncology 1000-gene panel (Human TME): biological interpretation and recommendations

Project context
- Inputs reviewed: final_panel_1000.csv and grouped.tsv; coverage summary and curation notes; UMAPs and RF confusion matrices derived from the 1000-gene panel; aggregate/method outputs.
- Audience: assay/biology co-development for a Vizgen-style spatial panel in human tumor microenvironment (TME).

1) How well the 1000-gene panel captures the human TME landscape

Summary across capabilities
- Immune lineage resolution (T/NK/B/plasma; macrophages/monocytes/DCs/neutrophils; Tregs/MDSC): strong
  - T cells: Core TCR complex and pan-T markers are present (CD3E, CD3D, TRAC/TRBC1/TRBC2). Subset/state axes include IL7R, CCR7, TCF7, KLRG1, CD69, CXCR3, CXCR5, IFNG, and cytotoxic effectors (NKG7, PRF1, GZMB, GZMK, GNLY).
  - NK cells: KLRD1 (CD94), NKG7, GZMB/GZMH/GZMK, PRF1, IFNG cover cytotoxic NK identity.
  - B cells and plasma cells: MS4A1 (CD20), CD79A, JCHAIN, IGKC/IGL constant chains, MZB1; genes supporting plasma differentiation (e.g., POU2AF1; IRF4/PRDM1 noted in coverage notes) and TLS readouts (CXCL13) are captured.
  - Monocytes/macrophages: LST1, FCGR3A, CSF1R, CD68, MS4A7, FCN1, AIF1, MARCO provide broad myeloid coverage with M1/M2 and TAM-associated markers.
  - Dendritic cells: signatures include CLEC9A (cDC1), LAMP3 (mature migratory DC) and CXCR3. (ITGAX/CD11c is a common DC/myeloid anchor; see minor gap below.)
  - Neutrophils/MDSC: S100A8, S100A9, FCN1 and OLR1 (PMN-MDSC) are present; S100A12 appears as an activation readout. (See minor gap on MPO/FCGR3B below.)
  UMAPs computed with only the 1000 genes show clear, spatially coherent clusters for these lineages; RF errors concentrate between closely related subtypes (e.g., T-cell subsets; DC/mono/mac), which is expected for fine-grained labels.

- Cytokine/chemokine states; exhaustion and activation axes: good breadth with some low-expression risks
  - Chemokines: CXCL1/2/3/8/9/10/11/12, CCL2/4/7/8/17/19/20, CXCL13, CXCL14 support trafficking and TLS biology. Receptors include CXCR3, CXCR4, CXCR6, CX3CR1, CCR2, CCR7.
  - Exhaustion/checkpoints: PDCD1 (PD-1), CTLA4, LAG3, HAVCR2 (TIM-3), TIGIT, TOX, BATF; TNFRSF4 (OX40) and TNFRSF18 (GITR) provide co-stimulation context; ADORA2A captures adenosine signaling. HLA class I/II coverage (HLA-A/B/C, HLA-DRA/DRB1/DP/DQ) supports antigen presentation and activation axes.
  - Note: Some interleukins (e.g., IL9, IL13, IL17A/F, IL22, IL37) are present but very lowly expressed in this dataset (<1% of cells), which can challenge spatial detectability.

- Malignant vs non-malignant discrimination; cancer pathways and stress programs: comprehensive
  - Epithelial/malignant identification: EPCAM, MUC1, TACSTD2, broad KRTs (KRT8/18/19; basal KRT5/14/17; KRT7), CLDN3/4; paired with pan-leukocyte PTPRC (CD45) to segregate tumor from immune.
  - Cancer pathways: strong coverage of RTK/MAPK/PI3K/JAK-STAT/TGF-β/WNT axes (EGFR/ERBB2/3; KRAS/BRAF; MAPK1/3; PIK3CA/PTEN/AKT/MTOR; JAK1/2/STAT1/3; TGFB1/TGFBR1/2/SMAD2/3/4; WNT5A/FZDs/CTNNB1; DKK1). These provide pathway-state readouts rather than mutation detection.
  - Stress/state programs: cell cycle (MKI67, TOP2A, PCNA, MCMs, E2F1), DNA damage (TP53, CDKN1A, ATM/ATR/CHEK1/2, BRCA1/2/PARP), proteotoxic/UPR and heat shock (HSPA1A/B, HSPH1, HSP90A/B, ATF4, DDIT3), hypoxia/angiogenesis (HIF1A, NDUFA4L2, VEGFA/FLT1/KDR/VWF/ANGPT2), EMT/ECM (VIM, FN1, SNAI/TWIST, COL1A1/1A2/3A1/4A1; SPARC/DCN), vasculature/pericytes (PECAM1/EMCN/VWF; RGS5) and fibroblast/CAF programs (TIMP1/3, MMP2/7/9/11, POSTN, CTHRC1, TAGLN, ACTA2).
  - UMAP using the panel separates malignant vs other; the RF malignant vs other confusion matrix, however, shows a modelling failure (all predicted as “Other”), indicating a training/class imbalance or label issue rather than a feature deficit.

2) Residual gaps/assay risks and concrete, minimal adjustments (≤20 genes)

Observed or anticipated risks
- Low expression cytokines: Several interleukins (IL9, IL13, IL17A/F, IL22, IL37) show <1% expression in this var=3k dataset. They may underperform in spatial detection (weak signal and probe competition). Consider swapping a subset for higher-yield functional readouts.
- Probe ambiguity from high homology families:
  - TCR/BCR constant regions (TRBC1/2; IGKC/IGLC2/IGLC3/IGHGP) and HLA class I paralogs can cross-hybridize; retain only the minimal nonredundant set and design probes on unique regions.
  - KRT paralogs (e.g., KRT8/18/19; KRT5/14/17), S100 family, WNT/FZD paralogs and mitochondrial rRNAs (MT-RNR1/2) require careful probe specificity QC.
- Myeloid and DC anchors: While myeloid coverage is strong, adding a few canonical anchors would further stabilize DC/mono–macrophage–neutrophil boundaries in spatial maps (see below).
- Malignant vs other RF failure: Given the UMAP separation with this panel, the confusion matrix likely reflects modelling (class imbalance, label mismatch/ leakage, insufficient malignant training cells) rather than panel insufficiency. We recommend re-training with class weighting, stratified CV and feature scaling checks.

Targeted adjustments (≤20 genes)
- Adds (12): strengthen DC/neutrophil and immunoregulation axes; include ligand checkpoints and adenosine pathway
  1) ITGAX (CD11c) – cDC/myeloid anchor
  2) MRC1 (CD206) – M2/TAM axis; spatial macrophage heterogeneity
  3) FCGR3B – neutrophil-specific FcγR to disambiguate from FCGR3A (NK/macrophage)
  4) MPO – neutrophil granule enzyme; robust PMN signal
  5) CD1C – cDC2 marker complementing CLEC9A (cDC1)
  6) ENTPD1 (CD39) – adenosine pathway on exhausted T cells/Tregs/TAMs
  7) NT5E (CD73) – adenosine pathway ecto-5′-nucleotidase on tumor/stroma/immune
  8) PDCD1LG2 (PD-L2) – PD-1 ligand on myeloid/DCs/tumor
  9) CD274 (PD-L1) – PD-1 ligand on tumor/myeloid/endothelium
  10) ICOSLG – Tfh–B and DC–T co-stimulation, TLS context
  11) CCR8 – Treg chemokine receptor associated with intratumoral Treg recruitment
  12) CLEC4C (BDCA2) – pDC marker (optional; if pDCs are present in the cohort)

- Drops/swaps (8): reduce low-yield redundancy to free space
  13) IL9 – drop (very low expression here; limited spatial yield)
  14) IL13 – drop (very low expression here)
  15) IL22 – drop (very low expression here)
  16) IL37 – drop (very low expression here)
  17) TRBC1 – drop (retain TRBC2 to reduce TCR-constant redundancy)
  18) IGLC3 – drop (retain IGKC and IGLC2 to limit IG constant redundancy)
  19) WNT1 – drop if present (retain pathway readouts WNT5A/CTNNB1/FZDs/DKK1)
  20) WNT7A – drop if present (as above)

Notes for implementation
- If CD274/PDCD1LG2/ICOSLG are already included, keep them and instead drop additional low-yield interleukins (e.g., IL17A/F) or one extra IG constant.
- Before finalizing, run in silico probe uniqueness checks for paralogous markers and confirm per-gene pct_expr in the downsampled adata.

3) Twenty sentinel genes with concise annotations (why they are critical here)
- CD3E – CD3ε, part of the TCR–CD3 complex; robust pan‑T marker delineating T lymphocyte infiltration. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=CD3E), UniProt (https://www.uniprot.org/uniprotkb?query=gene:CD3E%20AND%20organism_id:9606)
- TRAC – TCRα constant; marks TCR+ T cells and supports T-cell compartment mapping. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=TRAC), UniProt (https://www.uniprot.org/uniprotkb?query=gene:TRAC%20AND%20organism_id:9606)
- PRF1 – Perforin; cytolytic pore-former enabling granzyme entry, hallmark of CTL/NK activity. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=PRF1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:PRF1%20AND%20organism_id:9606)
- GZMB – Granzyme B; induces apoptosis in targets, key effector in anti-tumor immunity. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=GZMB), UniProt (https://www.uniprot.org/uniprotkb?query=gene:GZMB%20AND%20organism_id:9606)
- PDCD1 – PD‑1 inhibitory checkpoint on exhausted/activated T cells; central to immunotherapy biology. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=PDCD1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:PDCD1%20AND%20organism_id:9606)
- HAVCR2 – TIM‑3 checkpoint on T cells/myeloid cells; marks dysfunctional T cells in TME. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=HAVCR2), UniProt (https://www.uniprot.org/uniprotkb?query=gene:HAVCR2%20AND%20organism_id:9606)
- FOXP3 – Treg lineage TF; essential for mapping suppressive Treg niches. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=FOXP3), UniProt (https://www.uniprot.org/uniprotkb?query=gene:FOXP3%20AND%20organism_id:9606)
- IL2RA – CD25; high-affinity IL‑2 receptor on Tregs/activated T cells, complements FOXP3 for Treg. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=IL2RA), UniProt (https://www.uniprot.org/uniprotkb?query=gene:IL2RA%20AND%20organism_id:9606)
- MS4A1 – CD20; mature B-cell marker, supports B-lineage and TLS. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=MS4A1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:MS4A1%20AND%20organism_id:9606)
- MZB1 – ER chaperone in plasma cells; marks antibody-secreting cells in tumor beds. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=MZB1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:MZB1%20AND%20organism_id:9606)
- LST1 – Leukocyte-specific transcript; enriched in monocytes/macrophages and inflamed myeloid niches. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=LST1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:LST1%20AND%20organism_id:9606)
- FCGR3A – CD16a on NK cells and some macrophages; mediates ADCC; NK/myeloid boundary. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=FCGR3A), UniProt (https://www.uniprot.org/uniprotkb?query=gene:FCGR3A%20AND%20organism_id:9606)
- CLEC9A – DNGR‑1 on cDC1; key for cross‑presentation and CD8+ priming against tumors. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=CLEC9A), UniProt (https://www.uniprot.org/uniprotkb?query=gene:CLEC9A%20AND%20organism_id:9606)
- CXCL13 – B/Tfh‑attracting chemokine; hallmark of TLS and favorable anti‑tumor immunity in several cancers. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=CXCL13), UniProt (https://www.uniprot.org/uniprotkb?query=gene:CXCL13%20AND%20organism_id:9606)
- PECAM1 – CD31 endothelial adhesion molecule; vascular mapping and angiogenesis readout. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=PECAM1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:PECAM1%20AND%20organism_id:9606)
- KDR – VEGFR2; principal VEGF receptor on endothelium; anti‑angiogenic target/readout. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=KDR), UniProt (https://www.uniprot.org/uniprotkb?query=gene:KDR%20AND%20organism_id:9606)
- COL1A1 – Collagen I; CAF-derived ECM and desmoplasia signature. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=COL1A1), UniProt (https://www.uniprot.org/uniprotkb?query=gene:COL1A1%20AND%20organism_id:9606)
- EPCAM – Pan-epithelial/tumor cell marker; malignant vs immune boundary and tumor glandular mapping. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=EPCAM), UniProt (https://www.uniprot.org/uniprotkb?query=gene:EPCAM%20AND%20organism_id:9606)
- EGFR – RTK frequently upregulated in epithelial cancers; captures RTK/MAPK activation contexts. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=EGFR), UniProt (https://www.uniprot.org/uniprotkb?query=gene:EGFR%20AND%20organism_id:9606)
- MKI67 – Ki‑67 proliferation antigen; growth fraction of tumor and immune compartments. References: GeneCards (https://www.genecards.org/cgi-bin/carddisp.pl?gene=MKI67), UniProt (https://www.uniprot.org/uniprotkb?query=gene:MKI67%20AND%20organism_id:9606)

4) Evidence from embeddings and classifiers
- UMAPs (1000-gene panel only):
  - Broad labels (Immune vs Other) and detailed cell types form coherent, well‑separated clusters, consistent with adequate panel coverage of major TME lineages and states.
  - Malignant vs Other UMAP is separable, but RF classifier predicted only “Other” (complete off-diagonal collapse), pointing to modelling issues (class imbalance, label setup, or parameterization) rather than insufficient features.
- RF confusion (cell types): strong diagonals for common classes; misclassifications among closely related T-cell and myeloid subtypes; rare classes underperform (low support) — expected for fine-grained labels.

Recommendations for analysis workflow
- Re-train malignant vs other models with class weighting and stratified cross-validation; verify label provenance and balancing; consider simpler linear/regularized models for a sanity check.
- For spatial deployment, run probe-uniqueness QC on paralog families (TCR/BCR, KRT, HLA, S100, WNT/FZD) and review low-complexity regions.
- Validate detectability of low-expression interleukins in a pilot run; consider the swaps above if signals are weak.

Files and references
- Panel files: selection_expert/curated/final_panel_1000.csv; ..._grouped.tsv; coverage summary and curation notes in selection_expert/curated/.
- Figures: curated/figures/ (UMAPs; RF confusion matrices; method intersections).
- Sentinel gene references collected: biologist/report_browser_use_tme_sentinel_genes.md (with links) and biologist/references_1.bib.
