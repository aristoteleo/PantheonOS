---
id: gget_database_access
name: "gget: Genomic Database Querying"
description: |
  gget is a Python package and CLI tool with 23 interoperable modules for
  efficiently querying genomic databases including Ensembl, NCBI, UniProt,
  ARCHS4, Enrichr, COSMIC, OpenTargets, CellxGene, cBioPortal, PDB, and Bgee.
tags:
  - gget
  - database
  - Ensembl
  - BLAST
  - enrichment
  - COSMIC
  - OpenTargets
  - CellxGene
  - cBioPortal
  - sequence
---

# gget: Genomic Database Querying

**Citation**: Luebbert, L., & Pachter, L. (2023). Efficient querying of
genomic reference databases with gget. *Bioinformatics*.
https://doi.org/10.1093/bioinformatics/btac836

## Overview

gget is a free, open-source command-line tool and Python package with **23
interoperable modules**, each designed to query specific genomic databases.
All modules work as both Python functions and CLI commands.

### Module Categories

| Category | Modules | Databases |
|----------|---------|-----------|
| Reference data | `ref`, `search`, `info` | Ensembl, NCBI, UniProt |
| Sequence analysis | `seq`, `blast`, `blat`, `muscle`, `diamond` | Ensembl, NCBI BLAST, UCSC BLAT |
| Gene expression | `archs4`, `bgee`, `cellxgene` | ARCHS4, Bgee, CZ CELLxGENE |
| Enrichment | `enrichr` | Enrichr (KEGG, GO, ChEA, etc.) |
| Cancer/disease | `cosmic`, `opentargets`, `cbio` | COSMIC, OpenTargets, cBioPortal |
| Protein structure | `alphafold`, `pdb`, `elm` | AlphaFold, PDB, ELM |
| Mutation | `mutate` | — (local computation) |

---

## Installation

```bash
pip install --upgrade gget

# Or with uv (recommended)
uv pip install gget
```

---

## Cross-Module Conventions

| Convention | CLI | Python |
|------------|-----|--------|
| Save output | `-o path/to/file` | `save=True` |
| Suppress progress | `-q` / `--quiet` | `verbose=False` |
| CSV output | `-csv` / `--csv` | Default is DataFrame |
| JSON output | Default is JSON | `json=True` |

---

## Module Reference

### gget ref — Reference Genome Links

Fetch FTP download links and metadata for reference genomes and annotations
from Ensembl.

```python
gget.ref(species, which="all", release=None, ftp=False, list_species=False)
```

```bash
gget ref [-w gtf,dna] [-r RELEASE] SPECIES
```

**Key parameters**:

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `species` | positional | required | `genus_species` format. Shortcuts: `'human'`, `'mouse'` |
| `which` | `-w` | `'all'` | Comma-separated: `'gtf'`, `'cdna'`, `'dna'`, `'cds'`, `'pep'` |
| `release` | `-r` | latest | Ensembl release number (e.g., 110) |
| `list_species` | `-l` | False | List all available species |
| `ftp` | `-ftp` | False | Return only FTP links |
| `download` | `-d` | False | CLI only: download files via curl |

**Examples**:

```bash
# Get GTF and DNA links for human
gget ref -w gtf,dna homo_sapiens

# Integration with kallisto/bustools
kb ref -i index.idx -g t2g.txt -f1 fasta.fa $(gget ref --ftp -w dna,gtf homo_sapiens)
```

```python
gget.ref("homo_sapiens", which=["gtf", "dna"])
gget.ref(species=None, list_species=True, release=103)
```

---

### gget search — Gene Search

Search for genes and transcripts from Ensembl using free-form keywords.

```python
gget.search(searchwords, species, release=None, id_type="gene", andor="or", limit=None)
```

```bash
gget search -s SPECIES SEARCHWORDS
```

**Key parameters**:

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `searchwords` | positional | required | Free-form search terms (case-insensitive) |
| `species` | `-s` | required | Species in `genus_species` format |
| `id_type` | `-t` | `'gene'` | `'gene'` or `'transcript'` |
| `andor` | `-ao` | `'or'` | `'or'` = any word; `'and'` = all words |

**Return columns**: `ensembl_id`, `gene_name`, `ensembl_description`,
`biotype`, `url`

```bash
gget search -s human gaba gamma-aminobutyric
```

```python
gget.search(["gaba", "gamma-aminobutyric"], "homo_sapiens")
```

---

### gget info — Gene/Transcript Metadata

Fetch gene and transcript metadata from Ensembl, UniProt, and NCBI.

```python
gget.info(ens_ids, ncbi=True, uniprot=True, pdb=False)
```

```bash
gget info ENS_IDS
```

> [!WARNING]
> More than 1,000 IDs at once may cause server errors. Batch in smaller groups.

**Key parameters**:

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `ens_ids` | positional | required | Ensembl, WormBase, or FlyBase IDs |
| `ncbi` | `-n` | True | CLI flag **disables** NCBI results |
| `uniprot` | `-u` | True | CLI flag **disables** UniProt results |
| `pdb` | `-pdb` | False | Include PDB IDs (may slow query) |

```bash
gget info ENSG00000034713 ENSG00000104853
```

```python
gget.info(["ENSG00000034713", "ENSG00000104853"])
```

---

### gget seq — Sequence Retrieval

Retrieve nucleotide or amino acid sequences from Ensembl/UniProt.

```python
gget.seq(ens_ids, translate=False, isoforms=False)
```

```bash
gget seq [-t] [-iso] ENS_IDS
```

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `translate` | `-t` | False | Return amino acid sequences (from UniProt) |
| `isoforms` | `-iso` | False | Return all known transcript isoforms |

```python
gget.seq("ENSG00000034713", translate=True, isoforms=True)
```

---

### gget blast — BLAST Search

BLAST a nucleotide or amino acid sequence against NCBI databases.

```python
gget.blast(sequence, program=None, database=None, limit=50, expect=10.0)
```

```bash
gget blast SEQUENCE
```

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `sequence` | positional | required | Sequence string or path to FASTA file |
| `program` | `-p` | auto-detect | `blastn`, `blastp`, `blastx`, `tblastn`, `tblastx` |
| `database` | `-db` | auto-detect | `nt`, `nr`, `refseq_rna`, `swissprot`, etc. |
| `limit` | `-l` | 50 | Max number of hits |
| `expect` | `-e` | 10.0 | E-value threshold |

Auto-detection: nucleotide sequences use `blastn`/`nt`; amino acid sequences
use `blastp`/`nr`.

```python
gget.blast("MKWMFKEDHSLEHRCVESAKIR...")
gget.blast("fasta.fa")  # From file
```

---

### gget muscle — Multiple Sequence Alignment

Align multiple sequences using Muscle5.

```python
gget.muscle(fasta, super5=False, out=None)
```

```bash
gget muscle [-s5] FASTA
```

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `fasta` | positional | required | List of sequences or path to FASTA file |
| `super5` | `-s5` | False | Use Super5 algorithm (recommended for >100 sequences) |

```python
gget.muscle(["MSSSSWLLLSLVAVTAAQ...", "MSSSSWLLLSLVEVTAAQ..."])
gget.muscle("fasta.fa", out="aligned.afa")
```

---

### gget enrichr — Enrichment Analysis

Perform gene set enrichment analysis using Enrichr databases.

```python
gget.enrichr(genes, database, species="human", background_list=None,
             plot=False, kegg_out=None, kegg_rank=1)
```

```bash
gget enrichr -db DATABASE GENES
```

**Key parameters**:

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `genes` | positional | required | Gene symbols or Ensembl IDs |
| `database` | `-db` | required | Database name or shortcut (see below) |
| `species` | `-s` | `'human'` | `human`, `mouse`, `fly`, `yeast`, `worm`, `fish` |
| `background_list` | `-bkg_l` | None | Background genes for statistical rigor |
| `plot` | Python only | False | Plot top 15 results |
| `kegg_out` | `-ko` | None | Path for KEGG pathway PNG |

**Database shortcuts**:

| Shortcut | Resolves To |
|----------|-------------|
| `'pathway'` | KEGG_2021_Human |
| `'transcription'` | ChEA_2016 |
| `'ontology'` | GO_Biological_Process_2021 |
| `'diseases_drugs'` | GWAS_Catalog_2019 |
| `'celltypes'` | PanglaoDB_Augmented_2021 |
| `'kinase_interactions'` | KEA_2015 |

> [!TIP]
> Database shortcuts only work for human and mouse. For other species, use
> full database names.

```python
gget.enrichr(["ACE2", "AGT", "AGTR1"], database="ontology", plot=True)
gget.enrichr(["ZBP1", "IRF3", "RIPK1"], database="pathway",
             kegg_out="kegg.png", kegg_rank=1)
```

---

### gget archs4 — Expression Correlation & Tissue Atlas

Find correlated genes or tissue expression patterns using ARCHS4.

```python
gget.archs4(gene, which="correlation", species="human")
```

```bash
gget archs4 [-w correlation|tissue] GENE
```

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `gene` | positional | required | Gene symbol (e.g., `ACE2`) |
| `which` | `-w` | `'correlation'` | `'correlation'` (top 100 genes) or `'tissue'` (expression atlas) |
| `species` | `-s` | `'human'` | `'human'` or `'mouse'` (tissue mode only) |

```python
gget.archs4("ACE2")                       # Correlated genes
gget.archs4("ACE2", which="tissue")       # Tissue expression
```

---

### gget cosmic — Cancer Mutation Database

Search COSMIC for cancer mutations. Requires a COSMIC account for full
database downloads.

```python
# Query mode
gget.cosmic(searchterm, entity="mutations", limit=100,
            cosmic_tsv_path=None)

# Download mode
gget.cosmic(searchterm=None, download_cosmic=True,
            cosmic_project="cancer", grch_version=37)
```

```bash
# Download the database first
gget cosmic --download_cosmic --cosmic_project cancer

# Then query
gget cosmic EGFR --cosmic_tsv_path 'path/to/CancerMutationCensus.tsv'
```

**COSMIC projects**: `'cancer'` (~2GB), `'cancer_example'` (subset, no
account needed, ~2.5MB), `'census'` (~630MB), `'resistance'` (~1.6MB),
`'cell_line'` (~2.7GB)

> [!WARNING]
> Full COSMIC database downloads require a registered COSMIC account with
> email and password.

---

### gget opentargets — Drug-Target & Disease Associations

Fetch disease associations, drugs, tractability, and interaction data from
OpenTargets.

```python
gget.opentargets(ens_id, resource="diseases", limit=None, filters=None)
```

```bash
gget opentargets [-r RESOURCE] [-l LIMIT] ENS_ID
```

**Resource options**:

| Resource | Description |
|----------|-------------|
| `diseases` | Associated diseases (default) |
| `drugs` | Associated drugs (filter by `disease_id`) |
| `tractability` | Tractability data |
| `pharmacogenetics` | Pharmacogenetic responses (filter by `drug_id`) |
| `expression` | Gene expression by tissue (filter by `tissue_id`) |
| `depmap` | DepMap gene-disease effect |
| `interactions` | Protein-protein interactions |

```python
gget.opentargets("ENSG00000169194", resource="diseases", limit=5)
gget.opentargets("ENSG00000169194", resource="drugs", limit=2)
gget.opentargets("ENSG00000169194", resource="interactions",
                 filters={"protein_a_id": "P35225"}, limit=5)
```

---

### gget cellxgene — Single-Cell Data from CZ CELLxGENE

Query single-cell RNA-seq count matrices from CZ CELLxGENE Discover.

> [!TIP]
> Run `gget.setup("cellxgene")` (Python) or `gget setup cellxgene` (CLI)
> before first use.

```python
gget.cellxgene(species="homo_sapiens", gene=None, tissue=None,
               cell_type=None, disease=None, sex=None,
               meta_only=False, census_version="stable")
```

```bash
gget cellxgene [--gene GENES] [--tissue TISSUE] [--cell_type CELL_TYPE] -o output.h5ad
```

**Key filters** (all accept str or list):

| Filter | Description |
|--------|-------------|
| `gene` | Gene name(s) or Ensembl ID(s) |
| `tissue` | Tissue name(s) |
| `cell_type` | Cell type(s) |
| `disease` | Disease name(s) |
| `sex` | Sex filter |
| `development_stage` | Developmental stage(s) |
| `dataset_id` | CELLxGENE dataset ID(s) |

> [!WARNING]
> Gene symbols are case-sensitive. Use `'PAX7'` for human, `'Pax7'` for mouse.

**Returns**: AnnData object with count matrix and metadata.

```python
gget.setup("cellxgene")
adata = gget.cellxgene(
    gene=["ACE2", "ABCA1"],
    tissue="lung",
    cell_type=["mucus secreting cell", "neuroendocrine cell"]
)
```

```bash
gget cellxgene --gene ACE2 ABCA1 --tissue lung \
    --cell_type 'mucus secreting cell' -o lung_data.h5ad
```

---

### gget cbio — cBioPortal Cancer Genomics

Search studies and plot cancer genomics heatmaps using cBioPortal data.

```python
# Search for studies
gget.cbio_search(keywords)

# Plot heatmap
gget.cbio_plot(study_ids, genes, stratification="tissue",
               variation_type="mutation_occurrences", filter=None, dpi=100)
```

```bash
# Search
gget cbio search esophag ovary

# Plot
gget cbio plot -s msk_impact_2017 \
    -g AKT1 ALK NOTCH3 PDCD1 \
    -st tissue -vt mutation_occurrences -dpi 200
```

**Stratification options**: `'tissue'`, `'cancer_type'`,
`'cancer_type_detailed'`, `'study_id'`, `'sample'`

**Variation types**: `'mutation_occurrences'`, `'cna_nonbinary'` (requires
`sample` stratification), `'sv_occurrences'`, `'cna_occurrences'`,
`'Consequence'` (requires `sample` stratification)

---

### gget bgee — Cross-Species Expression

Fetch orthology and gene expression data across species from Bgee.

```python
gget.bgee(ens_id, type="orthologs")
```

```bash
gget bgee [-t orthologs|expression] ENS_ID
```

| Parameter | CLI | Default | Description |
|-----------|-----|---------|-------------|
| `ens_id` | positional | required | Ensembl gene ID(s) |
| `type` | `-t` | `'orthologs'` | `'orthologs'` or `'expression'` |

```python
gget.bgee("ENSSSCG00000014725")                    # Orthologs
gget.bgee("ENSSSCG00000014725", type="expression")  # Expression
```

---

### gget alphafold — Protein Structure Prediction

Fetch AlphaFold predicted protein structures.

```python
gget.alphafold(sequence_or_uniprot_id)
```

---

### gget pdb — Protein Data Bank

Query PDB for protein structures.

```python
gget.pdb(pdb_id)
```

---

### gget elm — Eukaryotic Linear Motifs

Predict eukaryotic linear motifs from amino acid sequences.

> [!TIP]
> Run `gget.setup("elm")` before first use.

```python
gget.setup("elm")
ortholog_df, regex_df = gget.elm(sequence, sensitivity="very-sensitive")
```

---

### gget diamond — Local Protein Alignment

Align protein sequences locally using DIAMOND (faster alternative to BLAST).

```python
gget.diamond(query, reference, sensitivity="very-sensitive", threads=1)
```

---

### gget mutate — Mutation Simulation

Apply mutations to nucleotide sequences and return mutated versions.

```python
gget.mutate(sequences, mutations, k=30)
```

```bash
gget mutate ATCGCTAAGCT -m 'c.4G>T'
```

---

## Common Workflows

### Gene Discovery to Enrichment

```python
import gget

# 1. Search for genes of interest
genes = gget.search(["dopamine", "receptor"], "homo_sapiens")

# 2. Get detailed metadata
info = gget.info(genes["ensembl_id"].tolist()[:10])

# 3. Find correlated genes
correlated = gget.archs4("DRD2")

# 4. Run enrichment analysis
enrichment = gget.enrichr(
    correlated["gene_symbol"].tolist()[:50],
    database="ontology",
    plot=True
)
```

### Sequence Analysis Pipeline

```python
import gget

# 1. Get sequences for genes of interest
seqs = gget.seq(["ENSG00000034713", "ENSG00000104853"])

# 2. BLAST to find homologs
hits = gget.blast(seqs[0])

# 3. Align multiple sequences
alignment = gget.muscle([seq1, seq2, seq3])
```

### Cancer Mutation Analysis

```python
import gget

# 1. Query OpenTargets for disease associations
diseases = gget.opentargets("ENSG00000169194", resource="diseases", limit=10)

# 2. Get drug information
drugs = gget.opentargets("ENSG00000169194", resource="drugs", limit=5)

# 3. Plot mutation landscape from cBioPortal
gget.cbio_plot(
    ["msk_impact_2017"],
    ["AKT1", "ALK", "NOTCH3", "PDCD1"],
    stratification="tissue",
    variation_type="mutation_occurrences"
)
```
