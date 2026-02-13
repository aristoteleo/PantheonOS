# TASK: A human kidney cancer gene profiling panel
Plex: 500-1000 genes with annotation, with genes grouped in major categories

Purpose: the gene panel should have the ability to catalog all cell types present in kidney tumors and adjacent renal tissue, enable immune profiling of the tumor microenvironment, and characterize malignant cell states based on kidney cancer–relevant signaling pathways. Specifically, the panel should be able to resolve immune cell types, distinguish stromal and vascular populations, differentiate malignant epithelial cells from normal renal epithelial compartments, profile key kidney cancer signaling pathways such as hypoxia/angiogenesis, metabolic reprogramming and proliferation, analyze cell states based on cytokine and interferon signaling, understand whether immune cells are activated or exhausted, and distinguish different tumor cell states or progression stages based on their expression of oncogenes or signaling molecules, so that an end user can comprehensively characterize the kidney tumor microenvironment and explore biologically and clinically relevant pathways.

Use:
- "/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/best_G_star.txt" as the seed subpanel.
- "/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/scores_kidney_filtered.csv" as pre-established gene scores to deduce algoritmics methods results.
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/do_not_touch_kidney_backup/preliminary_results/adata_kidney.h5ad (train dataset)
- the adatas in "/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/kidney_batches"  to benchmark (test dataset)

Building from the seed subpanel, complete to a panel by:
- Biological search/consensus filling

- Categorise biologically the genes in the final panel 