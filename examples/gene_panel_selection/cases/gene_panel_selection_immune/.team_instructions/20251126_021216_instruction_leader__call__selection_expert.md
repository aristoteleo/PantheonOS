Project: Immune-oncology 1000-gene panel (human TME)
Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Input data
- adata_path: /home/erwinpi/data/adata.h5ad (human tumor microenvironment scRNA-seq)
- Dataset source: bioRxiv 2024.01.17.576110

Environment context
- Environment summary is available at: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md
- Key stack: scanpy 1.11.5, anndata 0.11.4, scikit-learn 1.7.2, scvi-tools 1.3.3 (GPU), spapros 0.1.5, scGeneFit installed, squidpy 1.6.5. Hardware: 2x NVIDIA H100, ~188 GiB RAM. Disk space is somewhat tight (~51G free), so manage caches and intermediates conservatively.

Task (Step 1.c of workflow): Dataset understanding, QC, and potential downsampling
- Load the provided AnnData and perform a quick audit (n_obs, n_vars, layers, batches, modalities, key metadata fields).
- If the dataset has >50k cells, downsample to <50k cells (maintain representation of major batches/tissues) and save the downsampled AnnData. IMPORTANT: If downsampled, save the new adata path explicitly and treat it as the only input for the rest of the study.
- Perform essential QC: basic filtering (genes/cells), mitochondrial/ribosomal content assessment, doublet estimation check, normalization/log1p, HVGs for exploration. Do not perform heavy selection yet.
- Initial embedding/clustering and cell-type annotation pass using canonical markers to ensure that major TME compartments are identifiable (T/NK/B/plasma, myeloid lineages, stroma, malignant where applicable). Keep this light but informative.
- Save summary artifacts: figures (QC, UMAP), a short summary markdown, and a text file that contains only the definitive path to the AnnData object to be used for the rest of the study (downsampled path if applicable; otherwise the original path). Also include a machine-readable JSON/YAML with key dataset stats.

Biological context & downstream goals (for awareness; detailed selection instructions will follow next):
- Goal is a 1000-gene panel for human TME profiling enabling: resolution of major immune cell types (T/NK/B/plasma, macrophage/monocyte/DC/neutrophil, Tregs/MDSCs), cancer pathway states (oncogenes, tumor suppressors, cycle, DNA damage, hypoxia/angiogenesis/EMT), cytokine/chemokine states (IL/TNF/IFN, exhaustion/activation/cytotoxicity/inflammation), malignant vs non-malignant and subclones, and signaling (MAPK, PI3K, JAK-STAT, TGF-β, WNT), and cell states (exhaustion/activation/proliferation/senescence/stress). This context is for orienting the QC/annotation.

Deliverables
- Save all outputs under your subdir. Key expected files:
  - dataset_summary.md and dataset_summary.json/yaml
  - qc/*.png and embedding/*.png (UMAP/TSNE)
  - adata_active_path.txt containing the single path (absolute) to the AnnData object to use for all subsequent steps
  - markers_overview.tsv summarizing key marker expression across clusters
- Keep computations and memory usage mindful of disk/RAM constraints.
