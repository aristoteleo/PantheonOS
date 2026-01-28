I’ve completed the web search/crawl and compiled a curated, representative set of human solid tumor scRNA‑seq datasets with TME annotations from TISCH2, covering all requested cancer types. The full report (including dataset pages, direct download links, annotation fields, usage notes, and conversion suggestions) is saved in the workdir, with a BibTeX file for references.

Where to find the outputs
- Report: workdir/tisch2_scRNA_TME/report_browser_use_tisch2_scRNA_TME.md
- BibTeX references: workdir/tisch2_scRNA_TME/references_1.bib

Quick summary of selected datasets (all have curated TME annotations in TISCH2 and working direct download links for processed expression matrices and cell metadata)
Note: TISCH2 distributes processed expression as zipped text matrices + TSV metadata (not h5ad). I included conversion notes and alternatives in the report.

Melanoma (SKCM)
- SKCM_GSE115978_aPD1 (PMID: 30388455; 7,186 cells; Smart-seq2; primary+metastatic)
  - Page: http://tisch.comp-genomics.org/data/SKCM_GSE115978_aPD1
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Celltype (original), Cluster, Patient, Stage, Treatment, etc.
- SKCM_GSE120575_aPD1aCTLA4 (PMID: 30388456; 16,291 cells; Smart-seq2; metastatic)
  - Page: http://tisch.comp-genomics.org/data/SKCM_GSE120575_aPD1aCTLA4
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Response, Stage, TimePoint, Treatment

NSCLC
- NSCLC_GSE131907 (PMID: 32385277; 203,298 cells; 10x; primary+metastatic)
  - Page: http://tisch.comp-genomics.org/data/NSCLC_GSE131907
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Sample, Stage, TNMstage
- NSCLC_EMTAB6149 (PMID: 29988129; 40,218 cells; 10x; primary)
  - Page: http://tisch.comp-genomics.org/data/NSCLC_EMTAB6149
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor/original), Cluster, Source, Stage

Colorectal cancer (CRC)
- CRC_GSE146771_10X (PMID: 32302573; 43,817 cells; 10x; primary)
  - Page: http://tisch.comp-genomics.org/data/CRC_GSE146771_10X
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Sample, Stage, TNMstage
- CRC_GSE136394 (PMID: 31484655; 67,171 cells; 10x; primary+metastatic)
  - Page: http://tisch.comp-genomics.org/data/CRC_GSE136394
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Age, Sample, Source, Stage, Treatment

Breast cancer (incl. TNBC)
- BRCA_GSE176078 (PMID: 34493872; 89,471 cells; 10x; primary; includes TNBC)
  - Page: http://tisch.comp-genomics.org/data/BRCA_GSE176078
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor/original), Cluster, Celltype_major/minor/subset, Patient, Subtype
- BRCA_GSE114727_10X (PMID: 29961579; 28,678 cells; 10x; primary)
  - Page: http://tisch.comp-genomics.org/data/BRCA_GSE114727_10X
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Sample, Stage, TNMstage

Head and neck SCC (HNSC)
- HNSC_GSE103322 (PMID: 29198524; 5,902 cells; Smart-seq2; primary)
  - Page: http://tisch.comp-genomics.org/data/HNSC_GSE103322
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor/original), Cluster, Patient, Site, Stage, TNMstage
- HNSC_GSE139324 (PMID: 31924475; 130,721 cells; 10x; primary)
  - Page: http://tisch.comp-genomics.org/data/HNSC_GSE139324
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Disease, HPV status, Patient, Stage, TNMstage

Ovarian cancer (OV)
- OV_GSE151214 (PMID: 33852846; 59,446 cells; 10x; metastatic)
  - Page: http://tisch.comp-genomics.org/data/OV_GSE151214
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Sample, Source
- OV_GSE118828 (PMID: 30383866; 1,909 cells; Smart-seq2; primary+metastatic)
  - Page: http://tisch.comp-genomics.org/data/OV_GSE118828
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Age, Disease, Patient, Site, Source, Stage

Renal cell carcinoma (RCC, KIRC)
- KIRC_GSE111360 (PMID: 30550791; 23,130 cells; 10x; primary)
  - Page: http://tisch.comp-genomics.org/data/KIRC_GSE111360
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Sample, Source, Stage
- KIRC_GSE121636 (PMID: 33504936; 33,441 cells; 10x; primary)
  - Page: http://tisch.comp-genomics.org/data/KIRC_GSE121636
  - Downloads: Expression.zip, CellMetainfo_table.tsv, DE table
  - Annotations include: Celltype (malignancy/major/minor), Cluster, Patient, Sample, Tissue

Usage/licensing notes
- TISCH2 provides citation guidance but does not state an explicit license/redistribution policy on the site. Please cite:
  - Han et al., NAR 2023 (TISCH2) and Sun et al., NAR 2020 (TISCH)
- Redistribution/use should follow the original study repository terms (GEO/ArrayExpress) referenced by the accession in the dataset name (e.g., GSE…, E‑MTAB‑…).

If you need h5ad
- TISCH2 does not provide h5ad directly. Two practical routes:
  - Convert TISCH2 expression + metadata to AnnData: read text matrices + metadata TSV, align axes, adata.write_h5ad().
  - Alternatively, pull raw or processed matrices from the original accession (GEO/ArrayExpress). 10x data are often available as matrix.mtx + barcodes + features and can be read with scanpy.read_10x_mtx.

Everything above (including all visited links and per‑dataset annotation field lists) is fully detailed in the markdown report.

Paths for the reporter agent
- Report: workdir/tisch2_scRNA_TME/report_browser_use_tisch2_scRNA_TME.md
- BibTeX: workdir/tisch2_scRNA_TME/references_1.bib