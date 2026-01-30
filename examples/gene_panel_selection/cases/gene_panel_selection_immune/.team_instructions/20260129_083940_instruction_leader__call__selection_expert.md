Project: Human immune oncology gene profiling panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Task: Annotation refinement and category completion (no gene changes)

Goal
- The final panel must be fully annotated and grouped in major categories. Replace all “Unassigned” with specific categories and subcategories. Do not change the 1000-gene list.

Inputs
- Current panel: final_panel_1000.tsv and final_panel_1000_genes.txt
- Consensus/context: phase3/consensus_table.csv

Instructions
1) Keep the exact same 1000 HGNC genes. Do not add/remove genes.
2) For every gene with Category="Unassigned", assign a Category and Subcategory drawn from these major groups (extend subcategories as needed, but keep top-level categories consistent):
   - Lineage/Immune (T lineage; B lineage; NK lineage; Myeloid/DC; Neutrophil; Mast; Epithelial; Endothelial; Fibroblast/CAF)
   - Antigen presentation/Checkpoints (MHC I; MHC II; Processing; Co-inhibitory receptors; Co-stimulatory receptors; Ligands)
   - Cytokines/Chemokines/Receptors (Cytokines; Chemokines; Cytokine receptors; Chemokine receptors; Interferon signaling)
   - Signaling/Cancer pathways (MAPK/ERK; PI3K/AKT/mTOR; JAK/STAT; NF-κB; WNT; TGF-β; Notch; Hedgehog; Hippo; DNA damage/repair)
   - Oncogenes/Tumor suppressors/EMT (Oncogenes; Tumor suppressors; EMT/metastasis; Stemness)
   - Proliferation (Cell cycle checkpoints; Mitosis; DNA replication)
   - Metabolism/Stress (Glycolysis/OXPHOS; Hypoxia; Oxidative/antioxidant; Heat shock/Proteostasis; Autophagy/ER stress)
   - Spatial/TME interactions (Adhesion/integrins; ECM/matrix remodeling; Angiogenesis/vascular; CAF markers)
3) Add a concise, single-sentence Rationale for each gene (e.g., “CD79A: BCR component; marks mature B cells and plasmablast precursors.”). Use your knowledge and light programmatic heuristics (gene symbol patterns, GO terms) without altering the gene list.
4) Update outputs:
   - Write final_panel_1000_annotated.tsv (Gene, Category, Subcategory, Rationale, Methods_supporting, Consensus_score)
   - Update category_counts.json to reflect 100% coverage without “Unassigned”.
   - Regenerate final_panel_category_counts.(png|pdf)
   - Create panel_readme.md with a short description of categories, subcategories, and how to use the panel in practice.
5) Preserve the previous files; do not overwrite final_panel_1000.tsv. Add a note in panel_readme.md that final_panel_1000_annotated.tsv supersedes the earlier TSV for annotation completeness.

Proceed autonomously within these instructions and save all outputs in your workdir.