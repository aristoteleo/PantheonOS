"""RNA-seq Upstream Analysis - Data preparation, QC, alignment, and quantification"""

import os
import subprocess
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from ...utils.log import logger
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from ...utils.toolset import ToolSet, tool
from rich.console import Console

class RNASeqUpstreamToolSet(ToolSet):
    """RNA-seq Upstream Analysis Toolset - From FASTQ to quantified expression"""
    
    def __init__(
        self,
        name: str = "rna_upstream",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.pipeline_config = self._initialize_config()
        self.console = Console()
        
    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize RNA-seq pipeline configuration"""
        return {
            "file_extensions": {
                "raw_reads": [".fastq", ".fq", ".fastq.gz", ".fq.gz", ".fastq.bz2", 
                             ".fq.bz2", ".fastq.zst", ".fq.zst", ".sra"],
                "barcodes": [".whitelist.txt", ".tsv"],
                "genome": [".fa", ".fasta", ".fai", ".dict"],
                "transcriptome": [".fa", ".fasta", ".cdna.fa"],
                "star_index": [".tab", ".idx", ".sjdb"],
                "hisat2_index": [".ht2", ".ht2l"],
                "annotation": [".gtf", ".gff", ".gff3", ".bed"],
                "alignment": [".sam", ".bam", ".cram", ".bam.bai", ".cram.crai"],
                "quantification": [".txt", ".sf", ".tsv", ".h5", ".mtx"],
                "tracks": [".bw", ".bigwig", ".tdf"],
                "reports": [".html", ".json", ".txt", ".tsv", ".csv", ".pdf", ".png"]
            },
            "tools": {
                "acquisition": ["sra-tools", "pigz", "pbzip2", "zstd", "seqtk"],
                "qc": ["fastqc", "multiqc", "fastp", "trim_galore", "cutadapt", "rseqc"],
                "alignment": ["star", "hisat2", "bwa", "minimap2"],  # STAR first for RNA-seq
                "quantification": ["featurecounts", "kallisto", "rsem", "htseq-count"],
                "sam_processing": ["samtools", "sambamba", "picard"],
                "rna_qc": ["rseqc", "qualimap", "preseq", "dupradar"],
                "coverage": ["deeptools", "bedtools", "ucsc-tools"],
                "annotation": ["stringtie", "cufflinks", "gffcompare"]
            },
            "default_params": {
                "threads": 8,
                "memory": "16G",
                "quality_threshold": 20,
                "min_mapping_quality": 10,
                "strandedness": "unstranded",  # or "forward", "reverse"
                "read_length": 150
            }
        }
    

    @tool
    def RNA_Upstream(self, workflow_type: str, description: str = None):
        """Run a specific RNA-seq upstream workflow"""
        if workflow_type == "init":
            return self.run_upstream_workflow_init()
        elif workflow_type == "check_dependencies":
            return self.run_upstream_workflow_check_dependencies()
        elif workflow_type == "setup_genome_resources":
            return self.run_upstream_workflow_setup_genome_resources()
        elif workflow_type == "run_fastqc":
            return self.run_upstream_workflow_run_fastqc()
        elif workflow_type == "trim_adapters":
            return self.run_upstream_workflow_trim_adapters()
        elif workflow_type == "align_star":
            return self.run_upstream_workflow_align_star()
        elif workflow_type == "align_hisat2":
            return self.run_upstream_workflow_align_hisat2()
        elif workflow_type == "quantify_featurecounts":
            return self.run_upstream_workflow_quantify_featurecounts()
        elif workflow_type == "process_bam_smart":
            return self.run_upstream_workflow_process_bam_smart()
        elif workflow_type == "rna_qc":
            return self.run_upstream_workflow_rna_qc()
        else:
            return "Invalid workflow type"
    
    def run_upstream_workflow_init(self):
        """Run project initialization workflow"""
        logger.info("Running RNA-seq project initialization workflow")
        init_response = f"""
# Initialize RNA-seq Analysis Project

# Create RNA-seq project directory structure
mkdir -p rna_analysis/{{raw_data,trimmed_data,qc/{{fastqc,multiqc,rseqc}},alignment/{{star,hisat2}},quantification/{{featurecounts,htseq}},differential_expression,pathway_analysis,coverage/bigwig,visualization,reports,logs,scripts,references/{{genome,transcriptome,index/{{star,hisat2}}}}}}

# Create config file
cat > rna_analysis/rna_config.json << EOF
{{
  "project_name": "rna_analysis",
  "genome": "hg38", 
  "annotation": "gencode_v39",
  "paired_end": true,
  "strandedness": "unstranded",
  "created": "$(date)",
  "pipeline_version": "1.0.0"
}}
EOF

# Create sample sheet template for RNA-seq
cat > rna_analysis/samples.tsv << EOF
sample_id	fastq_r1	fastq_r2	condition	treatment	replicate	batch	library_type	strandedness
# RNA-seq Sample Examples:
# Ctrl_1	ctrl_1_R1.fastq.gz	ctrl_1_R2.fastq.gz	control	untreated	1	batch1	paired_end	unstranded
# Treat_1	treat_1_R1.fastq.gz	treat_1_R2.fastq.gz	treatment	drug_A	1	batch1	paired_end	unstranded
# Ctrl_2	ctrl_2_R1.fastq.gz	ctrl_2_R2.fastq.gz	control	untreated	2	batch1	paired_end	unstranded
# Treat_2	treat_2_R1.fastq.gz	treat_2_R2.fastq.gz	treatment	drug_A	2	batch1	paired_end	unstranded
EOF

echo "RNA-seq project structure created successfully!"
echo "Directory structure created with RNA-seq specific folders."
echo ""
echo "Next steps:"
echo "  1. Place FASTQ files in raw_data/"
echo "  2. Update samples.tsv with your sample information"  
echo "  3. Run: /bio rna upstream ./"
        """
        return init_response
    
    def run_upstream_workflow_check_dependencies(self):
        """Run dependency check workflow"""
        logger.info("Running RNA-seq dependency check workflow")
        check_dependencies_response = f"""
# Check RNA-seq Tool Dependencies

# Check core tools
which fastqc || echo "Missing: fastqc - conda install -c bioconda fastqc"
which STAR || echo "Missing: STAR - conda install -c bioconda star"
which hisat2 || echo "Missing: hisat2 - conda install -c bioconda hisat2"
which samtools || echo "Missing: samtools - conda install -c bioconda samtools"
which featureCounts || echo "Missing: featureCounts - conda install -c bioconda subread"
which trim_galore || echo "Missing: trim_galore - conda install -c bioconda trim-galore"
which multiqc || echo "Missing: multiqc - conda install -c bioconda multiqc"
which rseqc || echo "Missing: rseqc - conda install -c bioconda rseqc"

# Check versions
echo "Tool versions:"
fastqc --version 2>/dev/null | head -1
STAR --version 2>/dev/null
hisat2 --version 2>/dev/null | head -1
samtools --version 2>/dev/null | head -1
featureCounts -v 2>&1 | head -1
        """
        return check_dependencies_response
    
    def run_upstream_workflow_setup_genome_resources(self):
        """Run genome setup workflow"""
        logger.info("Running RNA-seq genome setup workflow")
        setup_genome_response = f"""
# Setup RNA-seq Genome Resources

# Download human genome (hg38)
wget -P genome/fasta/ https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz
gunzip genome/fasta/hg38.fa.gz

# Download GENCODE annotation
wget -P genome/gtf/ https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_39/gencode.v39.annotation.gtf.gz
gunzip genome/gtf/gencode.v39.annotation.gtf.gz

# Download transcriptome FASTA
wget -P genome/fasta/ https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_39/gencode.v39.transcripts.fa.gz
gunzip genome/fasta/gencode.v39.transcripts.fa.gz

# Build STAR index
STAR --runMode genomeGenerate \
    --genomeDir genome/index/star/ \
    --genomeFastaFiles genome/fasta/hg38.fa \
    --sjdbGTFfile genome/gtf/gencode.v39.annotation.gtf \
    --sjdbOverhang 149 \
    --runThreadN 8

# Build HISAT2 index
hisat2-build genome/fasta/hg38.fa genome/index/hisat2/hg38


# Create chromosome sizes file
samtools faidx genome/fasta/hg38.fa
cut -f1,2 genome/fasta/hg38.fa.fai > genome/hg38.chrom.sizes
        """
        return setup_genome_response
    
    def run_upstream_workflow_run_fastqc(self):
        """Run FastQC workflow"""
        logger.info("Running RNA-seq FastQC workflow")
        fastqc_response = f"""
# Run FastQC Quality Control

# Single-end reads
fastqc sample.fastq.gz -o qc/fastqc/

# Paired-end reads
fastqc sample_R1.fastq.gz sample_R2.fastq.gz -o qc/fastqc/

# Multiple samples
fastqc *.fastq.gz -o qc/fastqc/ -t 8

# Generate MultiQC summary report
multiqc qc/fastqc/ -o qc/multiqc/
        """
        return fastqc_response
    
    def run_upstream_workflow_trim_adapters(self):
        """Run adapter trimming workflow"""
        logger.info("Running RNA-seq adapter trimming workflow")
        trim_adapters_response = f"""
# Trim Adapters with Trim Galore

# Paired-end trimming
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/ --fastqc --length 20

# Single-end trimming  
trim_galore sample.fastq.gz -o fastq_trimmed/ --fastqc --length 20

# With quality and polyA trimming
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/ --quality 20 --length 20 --fastqc --polyA

# Alternative with cutadapt for RNA-seq
cutadapt -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCA -A AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT -q 20 --minimum-length 20 -o trimmed_R1.fastq.gz -p trimmed_R2.fastq.gz sample_R1.fastq.gz sample_R2.fastq.gz
        """
        return trim_adapters_response
    
    def run_upstream_workflow_align_star(self):
        """Run STAR alignment workflow"""
        logger.info("Running STAR alignment workflow")
        align_star_response = f"""
# RNA-seq Alignment with STAR

# Two-pass mapping for improved splice junction detection
# Pass 1: Initial alignment
STAR --genomeDir genome/index/star/ \
    --readFilesIn sample_R1_val_1.fq.gz sample_R2_val_2.fq.gz \
    --readFilesCommand zcat \
    --runThreadN 8 \
    --outFileNamePrefix sample_pass1_ \
    --outSAMtype BAM SortedByCoordinate \
    --outSAMunmapped Within \
    --outSAMattributes Standard

# Pass 2: Final alignment with discovered junctions
STAR --genomeDir genome/index/star/ \
    --readFilesIn sample_R1_val_1.fq.gz sample_R2_val_2.fq.gz \
    --readFilesCommand zcat \
    --runThreadN 8 \
    --outFileNamePrefix sample_ \
    --outSAMtype BAM SortedByCoordinate \
    --outSAMunmapped Within \
    --outSAMattributes Standard \
    --sjdbFileChrStartEnd sample_pass1_SJ.out.tab

# Index the final BAM
samtools index sample_Aligned.sortedByCoord.out.bam

# Get alignment statistics
samtools flagstat sample_Aligned.sortedByCoord.out.bam > sample_alignment_stats.txt

# Single-end version
STAR --genomeDir genome/index/star/ \
    --readFilesIn sample_trimmed.fq.gz \
    --readFilesCommand zcat \
    --runThreadN 8 \
    --outFileNamePrefix sample_ \
    --outSAMtype BAM SortedByCoordinate \
    --outSAMunmapped Within \
    --outSAMattributes Standard
        """
        return align_star_response
    
    def run_upstream_workflow_align_hisat2(self):
        """Run HISAT2 alignment workflow"""
        logger.info("Running HISAT2 alignment workflow")
        align_hisat2_response = f"""
# RNA-seq Alignment with HISAT2

# Paired-end alignment
hisat2 -x genome/index/hisat2/hg38 \
    -1 sample_R1_val_1.fq.gz \
    -2 sample_R2_val_2.fq.gz \
    -p 8 \
    --dta \
    --rna-strandness RF | samtools view -bS - > sample.bam

# Single-end alignment
hisat2 -x genome/index/hisat2/hg38 \
    -U sample_trimmed.fq.gz \
    -p 8 \
    --dta \
    --rna-strandness F | samtools view -bS - > sample.bam

# Sort and index BAM
samtools sort sample.bam -o sample_sorted.bam -@ 8
samtools index sample_sorted.bam

# Get alignment statistics
samtools flagstat sample_sorted.bam > sample_alignment_stats.txt
        """
        return align_hisat2_response
    
    def run_upstream_workflow_quantify_featurecounts(self):
        """Run featureCounts quantification workflow"""
        logger.info("Running featureCounts quantification workflow")
        quantify_featurecounts_response = f"""
# RNA-seq Quantification with featureCounts (subread package)

# Gene-level counting (recommended default)
featureCounts -a genome/gtf/gencode.v39.annotation.gtf \
    -o gene_counts.txt \
    -t exon \
    -g gene_id \
    -T 8 \
    -p \
    -s 0 \
    sample_sorted.bam

# Transcript-level counting
featureCounts -a genome/gtf/gencode.v39.annotation.gtf \
    -o transcript_counts.txt \
    -t exon \
    -g transcript_id \
    -T 8 \
    -p \
    -s 0 \
    -O \
    sample_sorted.bam

# Multi-sample counting (batch processing)
featureCounts -a genome/gtf/gencode.v39.annotation.gtf \
    -o all_samples_counts.txt \
    -t exon \
    -g gene_id \
    -T 8 \
    -p \
    -s 0 \
    sample1_sorted.bam sample2_sorted.bam sample3_sorted.bam

# Extract count matrix (remove first 6 columns with gene info)
cut -f1,7- all_samples_counts.txt > counts_matrix.txt
        """
        return quantify_featurecounts_response
    
    
    def run_upstream_workflow_process_bam_smart(self):
        """Run smart BAM processing workflow"""
        logger.info("Running smart BAM processing workflow")
        process_bam_smart_response = f"""
# Smart BAM Processing Pipeline for RNA-seq

# Complete processing from aligned BAM to analysis-ready BAM
# 1. Sort BAM by coordinate
samtools sort sample.bam -o sample_sorted.bam -@ 8

# 2. Index sorted BAM
samtools index sample_sorted.bam

# 3. Filter for quality and properly mapped reads
samtools view -b -q 10 -F 4 sample_sorted.bam > sample_filtered.bam

# 4. Mark duplicates (optional for RNA-seq, mainly for QC)
picard MarkDuplicates INPUT=sample_filtered.bam OUTPUT=sample_dedup.bam METRICS_FILE=dup_metrics.txt

# 5. Index final BAM
samtools index sample_dedup.bam

# 6. Generate comprehensive QC metrics
samtools flagstat sample_dedup.bam > sample_flagstat.txt
samtools stats sample_dedup.bam > sample_stats.txt
samtools idxstats sample_dedup.bam > sample_idxstats.txt

# 7. Generate coverage tracks
bamCoverage -b sample_dedup.bam -o sample.bw --normalizeUsing RPKM --binSize 10 -p 8

# 8. RNA-seq specific metrics
infer_experiment.py -r genome/gtf/gencode.v39.annotation.bed -i sample_dedup.bam > strandedness.txt
geneBody_coverage.py -r genome/gtf/gencode.v39.annotation.bed -i sample_dedup.bam -o sample_genebody
        """
        return process_bam_smart_response
    
    def run_upstream_workflow_rna_qc(self):
        """Run RNA-seq specific QC workflow"""
        logger.info("Running RNA-seq QC workflow")
        rna_qc_response = f"""
# RNA-seq Specific Quality Control

# RSeQC analysis suite
# 1. Read distribution across genomic features
read_distribution.py -i sample_sorted.bam -r genome/gtf/gencode.v39.annotation.bed > read_distribution.txt

# 2. Gene body coverage
geneBody_coverage.py -r genome/gtf/gencode.v39.annotation.bed -i sample_sorted.bam -o sample_genebody

# 3. Splice junction saturation
junction_saturation.py -i sample_sorted.bam -r genome/gtf/gencode.v39.annotation.bed -o sample_junction

# 4. Inner distance (for paired-end)
inner_distance.py -i sample_sorted.bam -o sample_inner_distance -r genome/gtf/gencode.v39.annotation.bed

# 5. RNA-seq strand specificity
infer_experiment.py -r genome/gtf/gencode.v39.annotation.bed -i sample_sorted.bam > strandedness.txt

# 6. rRNA content analysis
split_bam.py -i sample_sorted.bam -r genome/rRNA.bed -o sample_rrna

# Qualimap RNA-seq QC
qualimap rnaseq -bam sample_sorted.bam -gtf genome/gtf/gencode.v39.annotation.gtf -outdir qc/qualimap/sample

# Generate comprehensive MultiQC report
multiqc . -o qc/multiqc/ --title "RNA-seq QC Report"
        """
        return rna_qc_response