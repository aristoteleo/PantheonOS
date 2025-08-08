# Bioinformatics Tools: Natural Language to Tool Mapping

This guide provides comprehensive mappings for bioinformatics and ATAC-seq analysis tools in the Pantheon CLI. Use these tools to perform genomic data analysis through natural language requests.

---

## Bioinformatics & ATAC-seq Analysis

### Project Setup & Species Detection

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Start ATAC-seq analysis mode" | `/atac init` | Enters ATAC-seq analysis mode with tool access |
| "Analyze ATAC data in this folder" | `/atac upstream ./data_folder` | Runs complete upstream ATAC-seq analysis pipeline |
| "What files are in my data folder?" | `atac.scan_folder("./fastq_data")` | Comprehensively scans folder and identifies ATAC-seq files |
| "What species is this data from?" | `atac.auto_detect_species("./data_folder")` | Automatically detects species from filenames and FASTQ headers |
| "Set up a new ATAC project" | `atac.init("my_project", genome="hg38")` | Creates organized directory structure for ATAC-seq analysis |

### Genome Resources & References

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Download human genome reference" | `atac.setup_genome_resources("human", "hg38")` | Downloads genome FASTA, GTF annotations, and blacklist regions |
| "Show me available genomes" | `atac.list_available_resources()` | Lists all downloaded genome resources with status |
| "Check if my genome files are OK" | `atac.check_genome_integrity("human", "hg38")` | Validates integrity and completeness of genome files |
| "Clean up broken downloads" | `atac.clean_incomplete_downloads()` | Removes corrupted or incomplete download files |
| "Quick test setup" | `atac.quick_genome_setup("human")` | Sets up single chromosome for fast testing |
| "Get genome resource info" | `atac.get_resource_info("human", "hg38")` | Shows detailed information about genome resources |
| "Test download speeds" | `atac.test_download_speeds("hg38")` | Tests multiple download sources and finds fastest |

### Quality Control & Preprocessing

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Check data quality" | `atac.run_fastqc(["sample1_R1.fastq.gz", "sample1_R2.fastq.gz"])` | Runs FastQC quality control analysis on FASTQ files |
| "Remove adapters from reads" | `atac.trim_adapters("sample1_R1.fastq.gz", "sample1_R2.fastq.gz")` | Trims adapter sequences using Trim Galore |
| "Check if this FASTQ file is valid" | `atac.validate_fastq("sample.fastq.gz")` | Validates FASTQ format and provides basic statistics |

### Alignment & BAM Processing

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Align reads to genome automatically" | `atac.auto_align_fastq("./fastq_folder", genome_version="hg38")` | Fully automated alignment pipeline with tool installation |
| "Align with Bowtie2 manually" | `atac.align_bowtie2("/index/path", "R1.fastq.gz", "R2.fastq.gz")` | Manual Bowtie2 alignment (recommended for ATAC-seq) |
| "Align with BWA" | `atac.align_bwa("/index/path", "R1.fastq.gz", "R2.fastq.gz")` | BWA-MEM alignment (alternative to Bowtie2) |
| "Filter BAM file for quality" | `atac.filter_bam("sample.bam", min_quality=30)` | Filters alignments by quality and proper pairs |
| "Process BAM smartly" | `atac.process_bam_smart("sample.bam", remove_duplicates=False)` | Smart BAM processing with optional duplicate removal |
| "Remove duplicates with Picard" | `atac.mark_duplicates("sample.bam", remove_duplicates=True)` | Removes PCR duplicates using Picard tools |
| "Remove duplicates with samtools" | `atac.mark_duplicates_samtools("sample.bam")` | Alternative duplicate removal using samtools |

### Peak Calling & Analysis

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Find peaks with MACS2" | `atac.call_peaks_macs2("sample.bam", genome_size="hs")` | Standard peak calling using MACS2 |
| "Find peaks optimized for ATAC" | `atac.call_peaks_genrich(["sample1.bam"], "output_prefix")` | ATAC-seq optimized peak calling with Genrich |
| "Create coverage tracks" | `atac.bam_to_bigwig("sample.bam", normalize="RPKM")` | Generates BigWig coverage tracks for visualization |
| "Find enriched motifs" | `atac.find_motifs("peaks.narrowPeak", "hg38")` | Discovers enriched DNA motifs using HOMER |

### Quality Reports & Visualization

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Generate comprehensive QC report" | `atac.generate_atac_qc_report("sample.bam", "peaks.narrowPeak")` | Creates detailed QC report with MultiQC integration |
| "Make a heatmap" | `atac.plot_heatmap("matrix.gz", "heatmap.png")` | Generates heatmap visualization from matrix data |
| "Compute matrix for visualization" | `atac.compute_matrix(["sample.bw"], "peaks.bed", "matrix.gz")` | Computes matrix for heatmaps and profile plots |

### Tool Management

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Check what tools are installed" | `atac.check_dependencies()` | Shows installation status of all ATAC-seq tools |
| "Install missing tools" | `atac.install_missing_tools(["macs2", "deeptools"])` | Automatically installs specified bioinformatics tools |

---

## Single-Cell ATAC-seq (10x Genomics Cell Ranger ATAC)

### Setup & Installation

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Check Cell Ranger installation" | `cellranger_atac.check_installation()` | Verifies Cell Ranger ATAC setup and dependencies |
| "Initialize Cell Ranger project" | `cellranger_atac.init()` | Creates Cell Ranger ATAC project structure |
| "List available commands" | `cellranger_atac.list_commands()` | Shows all available Cell Ranger ATAC commands |

### Reference Genome Management

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Download human reference" | `cellranger_atac.download_reference("human", "GRCh38")` | Downloads pre-built human reference genome |
| "Download mouse reference" | `cellranger_atac.download_reference("mouse", "mm10")` | Downloads pre-built mouse reference genome |
| "List available references" | `cellranger_atac.list_available_references()` | Shows all pre-built reference genomes |
| "Build custom reference" | `cellranger_atac.auto_download_and_build_reference("zebrafish", "GRCz11")` | Auto-builds reference for custom organisms |
| "Show supported organisms" | `cellranger_atac.list_supported_organisms()` | Lists organisms available for auto-building |
| "Create reference package" | `cellranger_atac.mkref()` | Builds custom reference genome package |

### Data Validation

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Validate 10x FASTQ files" | `cellranger_atac.validate_fastq_10x()` | Validates FASTQ naming and 10x structure |
| "Check sequencing depth" | `cellranger_atac.validate_sequencing_depth()` | Checks if sequencing depth is adequate (25K-50K reads/cell) |
| "Assess run quality" | `cellranger_atac.validate_run_quality()` | Comprehensive quality assessment from metrics |

### Main Analysis Pipeline

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Run Cell Ranger ATAC analysis" | `cellranger_atac.count(run_id="sample1", fastqs="./fastqs", reference="./ref")` | Runs complete Cell Ranger ATAC pipeline |
| "Aggregate multiple samples" | `cellranger_atac.aggr()` | Combines multiple samples for comparative analysis |
| "Create aggregation CSV" | `cellranger_atac.create_aggr_csv()` | Creates CSV file for sample aggregation |
| "Demultiplex BCL files" | `cellranger_atac.mkfastq()` | Converts BCL files to FASTQ format |
| "Run test analysis" | `cellranger_atac.testrun()` | Runs test dataset to verify installation |

### Results Analysis

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Analyze Cell Ranger outputs" | `cellranger_atac.scan_run_outputs()` | Analyzes completed Cell Ranger runs |
| "View analysis summary" | `cellranger_atac.get_run_summary()` | Shows detailed run statistics and metrics |

---

## Bioinformatics Workflow Patterns

### Pattern 1: Complete ATAC-seq Analysis (Bulk)
```
User: "Analyze bulk ATAC-seq data in my folder"
CLI: /atac upstream ./data_folder
CLI: atac.scan_folder("./data_folder")
CLI: atac.auto_detect_species("./data_folder")
CLI: atac.setup_genome_resources("human", "hg38")
CLI: atac.auto_align_fastq("./data_folder", genome_version="hg38")
CLI: atac.process_bam_smart("sample.bam", remove_duplicates=False)
CLI: atac.call_peaks_macs2("filtered.bam", genome_size="hs")
CLI: atac.generate_atac_qc_report("filtered.bam", "peaks.narrowPeak")
```

### Pattern 2: Single-Cell ATAC-seq Analysis (10x)
```
User: "Analyze 10x single-cell ATAC data"
CLI: cellranger_atac.check_installation()
CLI: cellranger_atac.download_reference("human", "GRCh38")
CLI: cellranger_atac.validate_fastq_10x()
CLI: cellranger_atac.count(run_id="sample1", fastqs="./fastqs", reference="./GRCh38")
CLI: cellranger_atac.validate_run_quality()
CLI: cellranger_atac.scan_run_outputs()
```

### Pattern 3: Custom Organism Analysis
```
User: "Analyze ATAC data for zebrafish"
CLI: cellranger_atac.list_supported_organisms()
CLI: cellranger_atac.auto_download_and_build_reference("zebrafish", "GRCz11")
CLI: cellranger_atac.count(run_id="zebrafish_sample", fastqs="./fastqs", reference="./GRCz11_ref")
```

---

## Pro Tips for Bioinformatics Analysis

1. **Start with Species Detection** - Use `atac.auto_detect_species()` for automatic species identification
2. **Validate Data First** - Always validate FASTQ files before starting analysis
3. **Use Automated Pipelines** - Prefer `auto_align_fastq()` for streamlined processing
4. **Check Dependencies** - Use `check_dependencies()` or `check_installation()` before starting
5. **Quality Control** - Generate comprehensive QC reports for every analysis
6. **Resource Management** - Use `setup_genome_resources()` for organized reference management
7. **Single-cell vs Bulk** - Choose appropriate toolset based on your data type
8. **Test First** - Use test datasets or single chromosomes for pipeline validation

This bioinformatics guide helps you perform comprehensive genomic analysis using natural language commands in the Pantheon CLI.