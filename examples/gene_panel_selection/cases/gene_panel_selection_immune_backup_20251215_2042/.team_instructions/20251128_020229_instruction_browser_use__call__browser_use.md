Caller: selection_expert agent.
Task: For the following TISCH2 datasets, retrieve the dataset pages and extract the direct download URLs for processed expression and cell metadata files. Also capture any notes on file formats. Datasets:
- SKCM_GSE120575_aPD1aCTLA4
- NSCLC_EMTAB6149
- CRC_GSE146771_10X
- BRCA_GSE114727_10X
- HNSC_GSE103322
- OV_GSE151214
- KIRC_GSE111360
Deliverables:
- A markdown table with columns: dataset_id, page_url, expression_url(s), cellmeta_url, notes (file names inside zips, format like gz/tsv/matrix), and approximate cell count.
- Save the markdown to selection_expert/data_sources/tisch2_links.md under my workdir.
