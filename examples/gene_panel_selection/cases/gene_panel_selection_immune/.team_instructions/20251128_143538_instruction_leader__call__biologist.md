Project: Human immune oncology panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/biologist

Task: Phase 4 — Biological review of method outputs

Please:
- Read the method outputs from selection_expert Phase 3:
  - Gene lists per method (top-1000):
    HVG: selection_expert/results_phase3/gene_panels/hvg/hvg_top_1000.csv
    SpaPROS: selection_expert/results_phase3/gene_panels/spapros/spapros_top_1000.csv
    scGeneFit: selection_expert/results_phase3/gene_panels/scgenefit/scgenefit_top_1000.csv
    DE-consensus: selection_expert/results_phase3/gene_panels/de/de_top_1000.csv
    RF: selection_expert/results_phase3/gene_panels/rf/rf_top_1000.csv
- Inspect the venn overlap (figures_phase3/venn_top1000.png) and ARI/NMI/SI curves.

Goal:
- Provide a concise interpretation of the biology captured by each method’s top genes: which immune lineages/states/pathways are well covered vs underrepresented.
- Identify clear gaps relative to selection_goals.md (e.g., missing cytokines/chemokines, checkpoints, antigen presentation, DNA damage, EMT, hypoxia/angiogenesis, metabolic axes).
- Suggest a list of high-priority genes to add (up to ~300) to ensure complete coverage of:
  - cell-type markers; cytotoxicity and exhaustion modules
  - cytokines/chemokines and receptors
  - HLA/antigen presentation
  - cancer signaling pathways (RTK/RAS/MAPK, PI3K/AKT/mTOR, WNT, TGF-β, NOTCH, HIPPO, p53/MDM2, MYC, JAK/STAT/IFN, NF-κB)
  - EMT/hypoxia/angiogenesis/ECM/stroma
  - DNA damage/repair; apoptosis/autophagy
  - metabolism
  - housekeeping/controls

Deliverables:
- Markdown summary with bullet points of coverage and gaps.
- A CSV file biologist/proposed_additions_topup.csv with two columns: gene,category (category drawn from selection_goals.md scaffold).