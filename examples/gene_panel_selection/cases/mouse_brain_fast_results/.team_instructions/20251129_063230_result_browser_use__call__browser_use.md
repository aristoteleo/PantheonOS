Done. I compiled succinct, runnable Python snippets from the requested abc_atlas_access documentation and saved them here:
- Snippets: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/abc_usage_snippets.md
- Browser-use report: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/report_browser_use_abc_usage_snippets.md
- References (BibTeX): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/references_1.bib

What’s inside (high level)
- Initializing AbcProjectCache
- Listing datasets/directories (list_directories)
- Listing expression matrix files for WMB-10Xv3
- Listing metadata files for WMB-taxonomy
- Loading taxonomy metadata and joining cell type/class and region annotations
- Loading a subset of raw counts into AnnData without loading full matrices (backed='r' subsetting) and a helper to load specific genes across files (get_gene_data)
- Helpers for selecting cells by region/class/subclass/neighborhood
- Field names present in taxonomy/cell metadata (cell type labels, region labels, keys)

Notes on fields (also included in the snippets file)
- WMB-10X cell_metadata: cell_label, cell_barcode, barcoded_cell_sample_label, library_label, feature_matrix_label, entity, brain_section_label, library_method, region_of_interest_acronym, donor_label, donor_genotype, donor_sex, dataset_label, x, y, cluster_alias, abc_sample_id
- WMB-taxonomy (pivot): neurotransmitter, class, subclass, supertype, cluster
- WMB-taxonomy (colors): neurotransmitter_color, class_color, subclass_color, supertype_color, cluster_color
- WMB region_of_interest_metadata: acronym, label, name, order→region_of_interest_order, color_hex_triplet→region_of_interest_color
- Consensus integrated taxonomy: cell_to_cluster_membership (cell_label, cluster_alias); cluster (label, cluster_alias, number_of_cells); cluster_annotation_term_set (label, name, order, parent_term_set_label); cluster_annotation_term (label, name, cluster_annotation_term_set_name, color_hex_triplet, term_order, parent_term_label); cell_2d_embedding_coordinates (cell_label, x, y)

If you want any snippet expanded (e.g., directly creating AnnData from get_gene_data output with variable types preserved, or examples for WMB-10Xv2), I can add that.