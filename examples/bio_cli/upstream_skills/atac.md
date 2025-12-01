# ATAC-seq Upstream Analysis

## Overview

ATAC-seq (Assay for Transposase-Accessible Chromatin using sequencing) analysis pipeline for bulk chromatin accessibility profiling. This skill provides workflow-based commands and TodoList management for complete ATAC-seq data processing.

## Main Workflow

### Upstream Analysis

**Parameters:** `folder_path` - Target folder containing FASTQ files

**Global Rules:**
- Always use the provided `folder_path` in all phases
- Idempotent behavior: NEVER create duplicate todos. Only create if the list is EMPTY
- Do not ask the user for confirmations; proceed automatically and log warnings when needed
- After each concrete tool completes successfully, call `mark_task_done("what was completed")`, then `show_todos()`

### Phase 0 - Species Detection & Genome Resources

Use workflow commands for species detection and setup:
- `atac.ATAC_Upstream("init")` - Initialize project structure
- `atac.ATAC_Upstream("check_dependencies")` - Check tool availability
- `atac.ATAC_Upstream("setup_genome_resources")` - Setup genome resources

### Phase 1 - TODO Creation (Strict De-dup)

Mandatory order:
1. `current = show_todos()`
2. Analyze folder structure and FASTQ files

Creation rule (single condition):
- If current is EMPTY → create ONCE the following todos:
  - "Initialize ATAC-seq project structure"
  - "Check and install ATAC-seq dependencies"
  - "Setup genome resources and references"
  - "ATAC-seq Quality Control with FastQC"
  - "ATAC-seq Adapter Trimming"
  - "ATAC-seq Genome Alignment with Bowtie2"
  - "ATAC-seq BAM Filtering and Processing"
  - "ATAC-seq Peak Calling with MACS2"
  - "ATAC-seq Coverage Track Generation"
  - "ATAC-seq QC Report Generation"
- Else → DO NOT create anything. Work with the existing todos.

### Phase 2 - Execute with TODO Tracking (Loop)

**Critical Execution Strategy:**

When you call `atac.ATAC_Upstream()` or `atac.ATAC_Analysis()`, they return bash command templates. You MUST:

1. **Read and analyze** the entire returned bash command content carefully
2. **Understand the logic** and methodology described
3. **Adapt the provided commands** to your current data situation (file paths, sample names, etc.)
4. **Execute the adapted commands** using bash tool - NOT the original commands directly
5. **Handle errors** by adjusting commands based on the guidance provided
6. **Analyze results** - Check output files, logs, and success/failure status

**Result Analysis Requirement:**

After executing any bash commands:
1. **Analyze the output** - Don't just run and move on!
2. **Check generated files** - Verify expected output files were created
3. **Examine logs and errors** - Look for warnings, failures, or quality issues
4. **Validate results** - Are the results biologically reasonable?
5. **Make decisions** - Should parameters be adjusted based on what you observed?
6. **Document findings** - Note any issues or important observations

### Available Workflows

**Upstream Workflows** (`atac.ATAC_Upstream(workflow_type)`):
- `"init"` - Initialize project structure
- `"check_dependencies"` - Check tool dependencies
- `"setup_genome_resources"` - Setup genome resources
- `"run_fastqc"` - Quality control analysis
- `"trim_adapters"` - Adapter trimming
- `"align_bowtie2"` - Bowtie2 alignment (recommended for ATAC-seq)
- `"align_bwa"` - BWA alignment (alternative)
- `"filter_bam"` - BAM filtering
- `"mark_duplicates"` - Remove PCR duplicates
- `"process_bam_smart"` - Smart BAM processing pipeline

**Downstream Workflows** (`atac.ATAC_Analysis(workflow_type)`):
- `"call_peaks_macs2"` - MACS2 peak calling
- `"call_peaks_genrich"` - Genrich peak calling
- `"bam_to_bigwig"` - Generate coverage tracks
- `"compute_matrix"` - Compute matrix for plots
- `"plot_heatmap"` - Create heatmaps
- `"find_motifs"` - Motif analysis
- `"generate_atac_qc_report"` - Comprehensive QC report
- `"run_full_pipeline"` - Complete pipeline workflow

### Phase 3 - Adaptive TODO Refinement

- If dependencies missing → `add_todo("Install missing ATAC-seq tools")`
- If quality issues found → `add_todo("Address data quality issues")`
- If additional analysis needed → `add_todo("Additional analysis task")`

---

## Sub-tasks

### Peak Calling with MACS2

ATAC-seq Peak Calling with MACS2.

```bash
# Basic MACS2 peak calling
macs2 callpeak -t treatment.bam -n sample_name --outdir peaks/macs2 -g hs -q 0.01 --nomodel --shift -100 --extsize 200 -B --SPMR

# With control sample
macs2 callpeak -t treatment.bam -c control.bam -n sample_name --outdir peaks/macs2 -g hs -q 0.01 --nomodel --shift -100 --extsize 200 -B --SPMR

# For paired-end data
macs2 callpeak -t treatment.bam -n sample_name --outdir peaks/macs2 -g hs -q 0.01 --nomodel --shift -100 --extsize 200 -B --SPMR -f BAMPE

# Output files:
# - sample_name_peaks.narrowPeak
# - sample_name_summits.bed
# - sample_name_treat_pileup.bdg
```

### Peak Calling with Genrich

ATAC-seq Peak Calling with Genrich (ATAC-seq optimized).

```bash
# Basic Genrich peak calling
Genrich -t sample.bam -o sample.narrowPeak -q 0.01 -j -y -r -e chrM -v

# Multiple samples
Genrich -t sample1.bam,sample2.bam -o combined.narrowPeak -q 0.01 -j -y -r -e chrM -v

# With control samples
Genrich -t treatment1.bam,treatment2.bam -c control1.bam,control2.bam -o peaks.narrowPeak -q 0.01 -j -y -r -e chrM -v

# Parameters explained:
# -j: ATAC-seq mode
# -y: Remove PCR duplicates
# -r: Remove mitochondrial reads
# -e chrM: Exclude chromosome M
# -v: Verbose output
```

### Compute Matrix for deepTools Plots

```bash
# Reference-point mode (around peak centers)
computeMatrix reference-point -S sample.bw -R peaks.bed -o matrix.mat.gz -a 3000 -b 3000 -p 4

# Scale-regions mode (scale peaks to same size)
computeMatrix scale-regions -S sample.bw -R peaks.bed -o matrix.mat.gz -m 2000 -a 3000 -b 3000 -p 4

# Multiple BigWig files
computeMatrix reference-point -S sample1.bw sample2.bw -R peaks.bed -o matrix.mat.gz -a 3000 -b 3000 -p 4

# Multiple region files
computeMatrix reference-point -S sample.bw -R peaks.bed genes.bed -o matrix.mat.gz -a 3000 -b 3000 -p 4
```

### Plot Heatmap from Matrix

Plot heatmap from matrix using deepTools.

```bash
# Basic heatmap
plotHeatmap -m matrix.mat.gz -o heatmap.png --colorMap RdBu_r --whatToShow "heatmap and colorbar"

# With different colormap
plotHeatmap -m matrix.mat.gz -o heatmap.png --colorMap viridis --whatToShow "heatmap and colorbar"

# Advanced heatmap with clustering
plotHeatmap -m matrix.mat.gz -o heatmap.png --colorMap RdBu_r --whatToShow "heatmap and colorbar" --kmeans 3

# Profile plot instead of heatmap
plotProfile -m matrix.mat.gz -o profile.png --colors red blue
```

### Find Motifs with HOMER

Find enriched motifs using HOMER.

```bash
# Basic motif finding
findMotifsGenome.pl peaks.bed hg38 motifs_output/ -size 200 -mask

# With different genome
findMotifsGenome.pl peaks.bed mm10 motifs_output/ -size 200 -mask

# Larger motif search region
findMotifsGenome.pl peaks.bed hg38 motifs_output/ -size 500 -mask

# Known motif analysis only
findMotifsGenome.pl peaks.bed hg38 motifs_output/ -size 200 -mask -nomotif

# Custom background
findMotifsGenome.pl peaks.bed hg38 motifs_output/ -size 200 -mask -bg background_peaks.bed
```

### Generate ATAC-seq QC Report

Generate comprehensive ATAC-seq QC report.

```bash
# Basic alignment statistics
samtools flagstat sample.bam > sample_flagstat.txt

# Count peaks
wc -l peaks.narrowPeak

# Calculate FRiP (Fraction of Reads in Peaks)
bedtools intersect -a sample.bam -b peaks.narrowPeak -c | awk '{sum+=$NF} END {print sum}'

# Fragment size distribution
samtools view -f 2 sample.bam | awk '{print $9}' | awk '$1>0' | sort -n | uniq -c > fragment_sizes.txt

# TSS enrichment using deepTools
computeMatrix reference-point -S sample.bw -R tss.bed -o tss_matrix.mat.gz -a 2000 -b 2000 -p 4
plotProfile -m tss_matrix.mat.gz -o tss_enrichment.png

# Generate MultiQC report
multiqc --outdir reports/ --filename multiqc_report.html .
```

### Run Full Pipeline

Complete ATAC-seq Pipeline from FASTQ to Peaks.

```bash
# 1. Quality control
fastqc *.fastq.gz -o qc/fastqc/

# 2. Adapter trimming
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/

# 3. Alignment with Bowtie2
bowtie2 -x genome_index -1 sample_R1_val_1.fq.gz -2 sample_R2_val_2.fq.gz -p 4 | samtools view -bS - > sample.bam

# 4. Sort and index BAM
samtools sort sample.bam -o sample_sorted.bam
samtools index sample_sorted.bam

# 5. Filter BAM (remove unmapped, low quality, chrM)
samtools view -b -q 30 -F 4 sample_sorted.bam | samtools view -b - | grep -v chrM > sample_filtered.bam
samtools index sample_filtered.bam

# 6. Remove duplicates
picard MarkDuplicates INPUT=sample_filtered.bam OUTPUT=sample_dedup.bam METRICS_FILE=dup_metrics.txt REMOVE_DUPLICATES=true
samtools index sample_dedup.bam

# 7. Call peaks
macs2 callpeak -t sample_dedup.bam -n sample --outdir peaks/ -g hs -q 0.01 --nomodel --shift -100 --extsize 200 -B --SPMR -f BAMPE

# 8. Generate BigWig
bamCoverage -b sample_dedup.bam -o sample.bw --normalizeUsing RPKM --binSize 10 -p 4

# 9. QC report
multiqc . -o reports/
```

### Initialize Project

Initialize ATAC-seq Analysis Project.

```bash
# Create project directory structure
mkdir -p atac_analysis/{fastq,fastq_trimmed,qc/fastqc,qc/multiqc,alignment/{filtered,dedup},peaks/{macs2,genrich},coverage/bigwig,motifs,annotation,reports,logs,scripts}

# Create config file
cat > atac_analysis/atac_config.json << EOF
{
  "project_name": "atac_analysis",
  "genome": "hg38",
  "paired_end": true,
  "created": "$(pwd)",
  "pipeline_version": "1.0.0"
}
EOF

# Create sample sheet template
cat > atac_analysis/samples.tsv << EOF
sample_id	fastq_r1	fastq_r2	condition	replicate
# Example:
# Sample1	sample1_R1.fastq.gz	sample1_R2.fastq.gz	control	1
EOF
```

### Check Dependencies

Check ATAC-seq Tool Dependencies.

```bash
# Check core tools
which fastqc || echo "Missing: fastqc - conda install -c bioconda fastqc -y"
which bowtie2 || echo "Missing: bowtie2 - conda install -c bioconda bowtie2 -y"
which bwa || echo "Missing: bwa - conda install -c bioconda bwa -y"
which samtools || echo "Missing: samtools - conda install -c bioconda samtools -y"
which picard || echo "Missing: picard - conda install -c bioconda picard -y"
which macs2 || echo "Missing: macs2 - conda install -c bioconda macs2 -y"
which deeptools || echo "Missing: deeptools - conda install -c bioconda deeptools -y"
which trim_galore || echo "Missing: trim_galore - conda install -c bioconda trim-galore -y"

# Check versions
echo "Tool versions:"
fastqc --version 2>/dev/null | head -1
bowtie2 --version 2>/dev/null | head -1
samtools --version 2>/dev/null | head -1
macs2 --version 2>/dev/null
```

### Setup Genome Resources

Setup ATAC-seq Genome Resources.

```bash
# Download human genome (hg38)
wget -P genomes/ https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz
gunzip genomes/hg38.fa.gz

# Build Bowtie2 index
bowtie2-build genomes/hg38.fa genomes/hg38_bowtie2

# Build BWA index
bwa index genomes/hg38.fa

# Download blacklist regions
wget -P annotations/ https://github.com/Boyle-Lab/Blacklist/raw/master/lists/hg38-blacklist.v2.bed.gz

# Download GTF annotation
wget -P annotations/ https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_39/gencode.v39.annotation.gtf.gz

# Create chromosome sizes file
samtools faidx genomes/hg38.fa
cut -f1,2 genomes/hg38.fa.fai > genomes/hg38.chrom.sizes
```

### Run FastQC

Run FastQC Quality Control.

```bash
# Single-end reads
fastqc sample.fastq.gz -o qc/fastqc/

# Paired-end reads
fastqc sample_R1.fastq.gz sample_R2.fastq.gz -o qc/fastqc/

# Multiple samples
fastqc *.fastq.gz -o qc/fastqc/ -t 4

# Generate MultiQC summary report
multiqc qc/fastqc/ -o qc/multiqc/
```

### Trim Adapters

Trim Adapters with Trim Galore.

```bash
# Paired-end trimming
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/ --fastqc

# Single-end trimming
trim_galore sample.fastq.gz -o fastq_trimmed/ --fastqc

# With quality and length filtering
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/ --quality 20 --length 20 --fastqc

# Alternative with cutadapt
cutadapt -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCA -A AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT -q 20 --minimum-length 20 -o trimmed_R1.fastq.gz -p trimmed_R2.fastq.gz sample_R1.fastq.gz sample_R2.fastq.gz
```

### Align with Bowtie2

ATAC-seq Alignment with Bowtie2.

```bash
# Paired-end alignment (recommended for ATAC-seq)
bowtie2 -x genomes/hg38_bowtie2 -1 sample_R1_val_1.fq.gz -2 sample_R2_val_2.fq.gz -p 4 --very-sensitive --dovetail --no-mixed --no-discordant -I 10 -X 700 | samtools view -bS - > sample.bam

# Single-end alignment
bowtie2 -x genomes/hg38_bowtie2 -U sample_trimmed.fq.gz -p 4 --very-sensitive | samtools view -bS - > sample.bam

# Sort and index BAM
samtools sort sample.bam -o sample_sorted.bam -@ 4
samtools index sample_sorted.bam

# Get alignment statistics
samtools flagstat sample_sorted.bam > sample_alignment_stats.txt
```

### Align with BWA

ATAC-seq Alignment with BWA-MEM.

```bash
# Paired-end alignment
bwa mem -t 4 genomes/hg38.fa sample_R1_val_1.fq.gz sample_R2_val_2.fq.gz | samtools view -bS - > sample.bam

# Single-end alignment
bwa mem -t 4 genomes/hg38.fa sample_trimmed.fq.gz | samtools view -bS - > sample.bam

# Sort and index BAM
samtools sort sample.bam -o sample_sorted.bam -@ 4
samtools index sample_sorted.bam

# Get alignment statistics
samtools flagstat sample_sorted.bam > sample_alignment_stats.txt
```

### Filter BAM

Filter BAM Files for ATAC-seq.

```bash
# Remove unmapped, low quality, and mitochondrial reads
samtools view -b -q 30 -F 4 sample_sorted.bam | grep -v chrM | samtools view -b - > sample_filtered.bam

# Alternative with more specific filtering
samtools view -b -f 2 -q 30 sample_sorted.bam chr1 chr2 chr3 chr4 chr5 chr6 chr7 chr8 chr9 chr10 chr11 chr12 chr13 chr14 chr15 chr16 chr17 chr18 chr19 chr20 chr21 chr22 chrX chrY > sample_filtered.bam

# Remove blacklist regions
bedtools intersect -v -a sample_filtered.bam -b annotations/hg38-blacklist.v2.bed > sample_filtered_clean.bam

# Sort and index filtered BAM
samtools sort sample_filtered_clean.bam -o sample_filtered_sorted.bam
samtools index sample_filtered_sorted.bam
```

### Mark/Remove Duplicates

Mark/Remove PCR Duplicates.

```bash
# Using Picard MarkDuplicates
picard MarkDuplicates INPUT=sample_filtered_sorted.bam OUTPUT=sample_dedup.bam METRICS_FILE=dup_metrics.txt REMOVE_DUPLICATES=true

# Using sambamba (faster alternative)
sambamba markdup -r sample_filtered_sorted.bam sample_dedup.bam

# Using samtools (basic)
samtools markdup -r sample_filtered_sorted.bam sample_dedup.bam

# Sort and index deduplicated BAM
samtools sort sample_dedup.bam -o sample_dedup_sorted.bam
samtools index sample_dedup_sorted.bam

# Get final statistics
samtools flagstat sample_dedup_sorted.bam > sample_final_stats.txt
```

### Smart BAM Processing

Smart BAM Processing Pipeline.

```bash
# Complete processing from aligned BAM to analysis-ready BAM
# 1. Sort BAM
samtools sort sample.bam -o sample_sorted.bam -@ 4

# 2. Filter for quality, properly paired, remove chrM
samtools view -b -f 2 -q 30 sample_sorted.bam | grep -v -E "chrM|chrUn|random" > sample_filtered.bam

# 3. Remove blacklist regions
bedtools intersect -v -a sample_filtered.bam -b annotations/blacklist.bed > sample_clean.bam

# 4. Mark duplicates
picard MarkDuplicates INPUT=sample_clean.bam OUTPUT=sample_dedup.bam METRICS_FILE=dup_metrics.txt REMOVE_DUPLICATES=true

# 5. Final sort and index
samtools sort sample_dedup.bam -o sample_final.bam
samtools index sample_final.bam

# 6. Generate QC metrics
samtools flagstat sample_final.bam > sample_qc.txt
samtools stats sample_final.bam > sample_stats.txt

# 7. Fragment size distribution
samtools view -f 2 sample_final.bam | awk '{print $9}' | awk '$1>0' | sort -n | uniq -c > fragment_sizes.txt
```
