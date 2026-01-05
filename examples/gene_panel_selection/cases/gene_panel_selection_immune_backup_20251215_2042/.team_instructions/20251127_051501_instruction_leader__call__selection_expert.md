Project: Immune-oncology 1000-gene panel (Human TME)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Environment summary: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager/environment_summary.txt

Active dataset input (THE ONLY INPUT NOW):
/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad

High-level tasks for selection rounds:
Apply multiple complementary selection strategies and keep thorough records. For each method, save: method-specific parameters, a ranked gene list (CSV), figures, and a brief markdown note. Methods to run:
1) HVG-based list (already used for filtering, but derive top candidates with stability across subsamples)
2) Differential expression:
   - Across major immune cell types and malignant vs non-malignant
   - Use appropriate tests (e.g., Wilcoxon) and effect-size filtering
   - Derive robust markers per group; aggregate a ranked union
3) SpaPROS: spatial structure-aware selection tailored for TME niches
4) scGeneFit: optimize for separability of predefined classes (start with Cell_type_broad and refine if feasible)
5) Random Forest feature importance:
   - Multiclass classifier for cell_type
   - Binary classifier for Malignant vs Other
   - Use nested cross-validation and compute permutation importance; optionally SHAP if feasible

Aggregation and curation prep:
- Compile per-method outputs, create overlap visualization (Venn/UpSet) and an aggregate ranking score combining frequency, average rank, and cross-method consistency.
- Save panel drafts at sizes 500/800/1000/1200 genes for comparison, and evaluate UMAP separability using each draft list vs. full 3k background.

Deliverables for this phase:
- Per-method gene lists and notes in subfolders
- Overlap figure(s) and summary CSV of aggregate rankings
- Draft panel CSVs
- A short curation-ready markdown highlighting key gaps to fill for the final 1000-gene immune-oncology panel (e.g., missing cytokines or pathways)

Biological constraints/goals reminders:
- Ensure presence of lineage-defining markers (CD3D/E, TRAC, FOXP3, NKG7, MS4A1, SDC1/PRDM1/IRF4, FCGR3A, LST1, ITGAM, ITGAX, CLEC10A, CCR7, S100A8/A9, etc.).
- Include cytokines/chemokines and receptors (ILs, IFNs, TNFs; CXCL/CCL families; CCR/CXCR; exhaustion markers PDCD1, CTLA4, LAG3, HAVCR2, TIGIT; cytotoxicity GZMB/PRF1/IFNG; activation CD69, HLA genes; inflammation SPP1, IL1B, TNF).
- Capture cell cycle/DNA damage/proliferation (MKI67, TOP2A, PCNA, MCMs, E2Fs, TP53 pathway), stress (HSPA/HSPH, ATF4), hypoxia/angiogenesis/EMT (HIF1A, VEGFA, KDR, ANGPT2, VWF, EPCAM/KRTs, VIM, FN1, TWIST1/SNAI1/2), oncogenes/tumor suppressors (EGFR, KRAS, BRAF, PIK3CA, PTEN, RB1, MYC), core signaling (MAPK, PI3K, JAK-STAT, TGF-β, WNT).
- Use HGNC symbols and prefer genes with robust expression for Vizgen deployment.
