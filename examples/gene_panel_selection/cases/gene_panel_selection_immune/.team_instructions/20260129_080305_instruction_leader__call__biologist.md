Project: Human immune oncology gene profiling panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist

Task: Provide a biological interpretation of the final panel and key modules for the tumor microenvironment. Do NOT modify the gene selection; just interpret.

Inputs (from selection_expert workdir):
- Final panel (annotated): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/final_panel_1000.tsv
- Gene list: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/final_panel_1000_genes.txt
- Consensus table: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/phase3/consensus_table.csv
- Category counts figure: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/final_panel_category_counts.png
- Recap table: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/recap_table.tsv
- Benchmark summaries: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/phase4_benchmarking_summary.md

Scope of interpretation
- How the panel supports:
  1) Cell type cataloging (immune, stromal, endothelial, malignant) and fine-grained immune subsets (CD4/CD8/Treg/Th1/Th2/Th17/Tfh; NK; B/plasma; myeloid/DC subsets, neutrophils, mast).
  2) Immune checkpoints and antigen presentation (e.g., PDCD1, CD274, CTLA4, LAG3, HAVCR2, TIGIT; HLA genes; CD80/CD86; PVR/NECTIN axes).
  3) Cytokine/chemokine axes and receptors (IL/IFN/TNF families; CCL/CXCL and CCR/CXCR; IFN/JAK/STAT modules).
  4) T cell exhaustion/activation markers (e.g., PDCD1, HAVCR2, LAG3, TIGIT, TOX, CXCL13, PRDM1, IKZF2) and proliferation.
  5) Cancer signaling pathways and oncogenic programs (MAPK/ERK, PI3K/AKT/mTOR, WNT, TGF-β, JAK/STAT, NF-κB, Notch, Hedgehog, DDR; EMT/metastasis; stemness), plus metabolism/stress and TME interactions (ECM/adhesion/angiogenesis/CAF).

Deliverable
- Write a concise interpretation in: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist/biological_interpretation.md
- Organize by the bullets above. Call out representative genes from the final panel for each function. Where helpful, cite well-known markers concisely (no web search required).
- Do not edit selection files; this is narrative interpretation only.