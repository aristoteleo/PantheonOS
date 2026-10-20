---
id: iseq_data_download
name: "iSeq: Sequencing Data Download"
description: |
  iSeq is a unified Bash CLI tool for downloading sequencing data and metadata
  from five public databases (GSA, SRA, ENA, DDBJ, GEO) through a single
  interface. Supports parallel downloads, Aspera transfers, MD5 verification,
  and automatic SRA-to-FASTQ conversion.
tags:
  - iSeq
  - download
  - SRA
  - ENA
  - GSA
  - DDBJ
  - GEO
  - FASTQ
  - sequencing
---

# iSeq: Sequencing Data Download

**Latest version**: 1.9.8 | **License**: MIT

**Citation**: Haoyu Chao, Zhuojin Li, Dijun Chen, Ming Chen. "iSeq: An
integrated tool to fetch public sequencing data," *Bioinformatics*, 2024,
btae641.

## Overview

iSeq is a **pure Bash CLI tool** (not a Python package) for downloading
sequencing data and metadata from multiple public databases through a single
unified interface. It auto-detects the database from accession prefixes and
handles retries, MD5 verification, and format conversion automatically.

> [!WARNING]
> iSeq is a shell script, not a Python package. It must be invoked from the
> command line (`iseq` command). There is no Python API.

---

## Supported Databases & Accession Formats

| Database | BioProject | Study | BioSample | Sample | Experiment | Run |
|----------|-----------|-------|-----------|--------|------------|-----|
| **GSA** | `PRJC*` | `CRA*` | `SAMC*` | — | `CRX*` | `CRR*` |
| **SRA** | `PRJNA*` | `SRP*` | `SAMN*` | `SRS*` | `SRX*` | `SRR*` |
| **ENA** | `PRJEB*` | `ERP*` | `SAME*` | `ERS*` | `ERX*` | `ERR*` |
| **DDBJ** | `PRJDB*` | `DRP*` | `SAMD*` | `DRS*` | `DRX*` | `DRR*` |
| **GEO** | `GSE*` | — | `GSM*` | — | — | — |

GEO accessions are resolved internally to their associated SRA/ENA
identifiers.

---

## Installation

### Conda (Recommended)

```bash
conda install bioconda::iseq
```

If channel conflicts occur:

```bash
conda install -c conda-forge -c bioconda iseq
```

### From Source

```bash
version="1.9.8"
wget "https://github.com/BioOmics/iSeq/releases/download/v${version}/iSeq-v${version}.tar.gz"
tar -zvxf "iSeq-v${version}.tar.gz"
cd iSeq-v${version}/bin/
chmod +x iseq
echo 'export PATH=$PATH:'$(pwd) >> ~/.bashrc
source ~/.bashrc
```

### Dependencies

| Tool | Version | Purpose |
|------|---------|---------|
| pigz | >= 2.8 | Multi-threaded gzip compression |
| wget | >= 1.16 | HTTP/FTP file download |
| axel | >= 2.17 | Multi-threaded downloading |
| aspera-cli | = 4.14.0 | High-speed Aspera transfers |
| sra-tools | >= 2.11.0 | SRA download link retrieval and FASTQ conversion |

Install all dependencies via conda:

```bash
conda create -n iseq -c conda-forge -c bioconda pigz wget axel aspera-cli sra-tools
```

---

## CLI Reference

### Usage

```bash
iseq -i <accession_or_file> [options]
```

### Parameters

| Flag | Long | Argument | Default | Description |
|------|------|----------|---------|-------------|
| `-i` | `--input` | text/file | required | Accession ID or file with IDs (one per line) |
| `-o` | `--output` | path | `.` | Output directory |
| `-m` | `--metadata` | — | off | Fetch metadata only, skip data download |
| `-g` | `--gzip` | — | off | Download FASTQ in gzip format (`.fastq.gz`) |
| `-q` | `--fastq` | — | off | Download SRA and convert to FASTQ |
| `-t` | `--threads` | int | 8 | Threads for SRA-to-FASTQ conversion (max ~15) |
| `-p` | `--parallel` | int | 1 | Parallel download connections |
| `-a` | `--aspera` | — | off | Use Aspera high-speed transfer |
| `-d` | `--database` | ena/sra | ena | Source database for SRA data |
| `-e` | `--merge` | ex/sa/st | off | Merge FASTQs: experiment/sample/study level |
| `-s` | `--speed` | int (MB/s) | 1000 | Download speed limit |
| `-k` | `--skip-md5` | — | off | Skip MD5 checksum verification |
| `-r` | `--protocol` | ftp/https | ftp | Protocol for ENA downloads |
| `-Q` | `--quiet` | — | off | Suppress download progress bars |
| `-v` | `--version` | — | — | Show version |
| `-h` | `--help` | — | — | Show help |

---

## Usage Examples

### Basic Downloads

```bash
# Download all runs for a project
iseq -i PRJNA211801

# Download from GSA (China)
iseq -i CRA000553

# Download from GEO
iseq -i GSE122139

# Single experiment or run
iseq -i SRX477044
iseq -i SRR1178105

# Specify output directory
iseq -i SRR931847 -o PRJNA211801
```

### Metadata Only

```bash
iseq -i PRJNA211801 -m
```

### Download Formats

```bash
# Gzip-compressed FASTQ directly
iseq -i SRR1178105 -g

# Download SRA and convert to FASTQ
iseq -i SRR1178105 -q

# Force SRA database (instead of ENA default)
iseq -i SRR1178105 -d sra
```

### Performance Options

```bash
# 10 parallel connections
iseq -i SRR1178105 -p 10

# Aspera high-speed transfer
iseq -i SRR1178105 -a

# GSA + Aspera (prioritizes HUAWEI Cloud)
iseq -i CRR311377 -a
```

### FASTQ Merging

```bash
# Merge at experiment level
iseq -i CRX020217 -e ex

# Merge at sample level
iseq -i SAMC017083 -e sa

# Merge at study level
iseq -i PRJCA000613 -e st
```

### Batch Processing

```bash
# Process accessions from a file (one per line)
iseq -i SRR_Acc_List.txt -a -g
```

### Combined Workflows

```bash
# Parallel + FASTQ conversion + gzip + experiment merge
iseq -i SRX2993509 -q -g -p 10 -e ex

# Aspera + gzip FASTQ + experiment merge for whole project
iseq -i PRJNA211801 -a -g -e ex
```

---

## Output Files

### SRA/ENA/DDBJ/GEO

| File | Description |
|------|-------------|
| `*.sra` or `*.fastq.gz` | Sequencing data files |
| `${accession}.metadata.tsv` | Metadata (~191 columns via ENA API) |
| `${accession}.metadata.csv` | Fallback metadata (~30 columns from SRA) |
| `success.log` | Successfully downloaded files |
| `fail.log` | Failed downloads with details |

### GSA

| File | Description |
|------|-------------|
| `*.gz` | Sequencing data files |
| `${accession}.metadata.csv` | Metadata (~25 columns via getRunInfo API) |
| `${accession}.metadata.xlsx` | Supplementary metadata (3 sheets: Sample, Experiment, Run) |
| `success.log` / `fail.log` | Download logs |

---

## Key Behaviors

1. **Automatic re-run detection**: Running the same command again skips
   previously downloaded files
2. **Automatic retry**: Failed downloads retry up to 3 times
3. **MD5 verification**: Files verified by checksum after download (disable
   with `-k`)
4. **Partial cleanup**: Failed MD5 verification deletes partial files
5. **Error resilience**: Batch downloads continue despite individual failures
6. **GEO resolution**: GSE/GSM accessions resolve to SRA/ENA identifiers
   automatically
7. **ENA fallback**: When ENA is inaccessible, auto-switches to alternative
   database
8. **Resumable downloads**: Interrupted downloads can be resumed

---

## Best Practices

1. **Start with metadata**: Use `-m` to preview what will be downloaded
2. **Use Aspera for large projects**: `-a` significantly speeds up downloads
3. **Enable parallel connections**: `-p 10` for faster multi-file downloads
4. **Download compressed**: Use `-g` to save disk space and bandwidth
5. **Batch from file**: Put accessions in a text file for large-scale downloads
6. **Check logs**: Review `fail.log` after batch downloads for any failures
7. **Update regularly**: Run `conda update iseq` to stay current with API
   changes
