"""ATAC-seq Upstream Analysis - Data preparation, QC, alignment, and BAM processing"""

from pathlib import Path
from typing import Dict, Any
from ...utils.log import logger
from ....toolset import ToolSet, tool
from rich.console import Console


class ATACSeqUpstreamToolSet(ToolSet):
    """ATAC-seq Upstream Analysis Toolset - From FASTQ to filtered BAM files"""
    
    def __init__(
        self,
        name: str = "atac_upstream",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.pipeline_config = self._initialize_config()
        self.console = Console()
        
    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize ATAC-seq pipeline configuration"""
        return {
            "file_extensions": {
                "raw_reads": [".fastq", ".fq", ".fastq.gz", ".fq.gz", ".fastq.bz2", 
                             ".fq.bz2", ".fastq.zst", ".fq.zst", ".sra"],
                "barcodes": [".whitelist.txt", ".tsv"],
                "genome": [".fa", ".fasta", ".fai", ".dict"],
                "bwa_index": [".amb", ".ann", ".bwt", ".pac", ".sa"],
                "bowtie2_index": [".bt2", ".bt2l"],
                "regions": [".bed", ".bed.gz", ".chrom.sizes"],
                "alignment": [".sam", ".bam", ".cram", ".bam.bai", ".cram.crai"],
                "peaks": [".narrowPeak", ".broadPeak", ".gappedPeak", ".xls", ".bedgraph", ".bdg"],
                "tracks": [".bw", ".bigwig", ".tdf"],
                "reports": [".html", ".json", ".txt", ".tsv", ".csv", ".pdf", ".png"]
            },
            "tools": {
                "acquisition": ["sra-tools", "pigz", "pbzip2", "zstd", "seqtk"],
                "qc": ["fastqc", "multiqc", "fastp", "trim_galore", "cutadapt"],
                "alignment": ["bowtie2", "bwa", "bwa-mem2", "minimap2"],  # Bowtie2 first for ATAC-seq
                "sam_processing": ["samtools", "sambamba", "picard", "samblaster"],
                "atac_qc": ["ataqv", "preseq", "deeptools"],
                "peak_calling": ["macs2", "genrich", "hmmratac"],
                "coverage": ["deeptools", "bedtools", "ucsc-tools"],
                "annotation": ["homer", "meme", "chipseeker", "bedtools"]
            },
            "default_params": {
                "threads": 4,
                "memory": "8G",
                "quality_threshold": 20,
                "min_mapping_quality": 30,
                "fragment_size_range": [50, 1000],
                "peak_calling_fdr": 0.01
            }
        }
    

    @tool
    def ATAC_Upstream(self, workflow_type: str, description: str = None):
        """Run a specific ATAC-seq upstream workflow"""
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
        elif workflow_type == "align_bowtie2":
            return self.run_upstream_workflow_align_bowtie2()
        elif workflow_type == "align_bwa":
            return self.run_upstream_workflow_align_bwa()
        elif workflow_type == "filter_bam":
            return self.run_upstream_workflow_filter_bam()
        elif workflow_type == "mark_duplicates":
            return self.run_upstream_workflow_mark_duplicates()
        elif workflow_type == "process_bam_smart":
            return self.run_upstream_workflow_process_bam_smart()
        else:
            return "Invalid workflow type"
    
    def run_upstream_workflow_init(self):
        """Run project initialization workflow"""
        logger.info("Running project initialization workflow")
        init_response = f"""
# Initialize ATAC-seq Analysis Project

# Create project directory structure
mkdir -p atac_analysis/{{fastq,fastq_trimmed,qc/fastqc,qc/multiqc,alignment/{{filtered,dedup}},peaks/{{macs2,genrich}},coverage/bigwig,motifs,annotation,reports,logs,scripts}}

# Create config file
cat > atac_analysis/atac_config.json << EOF
{{
  "project_name": "atac_analysis",
  "genome": "hg38", 
  "paired_end": true,
  "created": "$(pwd)",
  "pipeline_version": "1.0.0"
}}
EOF

# Create sample sheet template  
cat > atac_analysis/samples.tsv << EOF
sample_id	fastq_r1	fastq_r2	condition	replicate
# Example:
# Sample1	sample1_R1.fastq.gz	sample1_R2.fastq.gz	control	1
EOF
        """
        return init_response
    
    def run_upstream_workflow_check_dependencies(self):
        """Run dependency check workflow"""
        logger.info("Running dependency check workflow")
        check_dependencies_response = f"""
# Check ATAC-seq Tool Dependencies

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
        """
        return check_dependencies_response
    
    def run_upstream_workflow_setup_genome_resources(self):
        """Run genome setup workflow"""
        logger.info("Running genome setup workflow")
        setup_genome_response = f"""
# Setup ATAC-seq Genome Resources

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
        """
        return setup_genome_response
    
    def run_upstream_workflow_run_fastqc(self):
        """Run FastQC workflow"""
        logger.info("Running FastQC workflow")
        fastqc_response = f"""
# Run FastQC Quality Control

# Single-end reads
fastqc sample.fastq.gz -o qc/fastqc/

# Paired-end reads
fastqc sample_R1.fastq.gz sample_R2.fastq.gz -o qc/fastqc/

# Multiple samples
fastqc *.fastq.gz -o qc/fastqc/ -t 4

# Generate MultiQC summary report
multiqc qc/fastqc/ -o qc/multiqc/
        """
        return fastqc_response
    
    def run_upstream_workflow_trim_adapters(self):
        """Run adapter trimming workflow"""
        logger.info("Running adapter trimming workflow")
        trim_adapters_response = f"""
# Trim Adapters with Trim Galore

# Paired-end trimming
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/ --fastqc

# Single-end trimming  
trim_galore sample.fastq.gz -o fastq_trimmed/ --fastqc

# With quality and length filtering
trim_galore --paired sample_R1.fastq.gz sample_R2.fastq.gz -o fastq_trimmed/ --quality 20 --length 20 --fastqc

# Alternative with cutadapt
cutadapt -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCA -A AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT -q 20 --minimum-length 20 -o trimmed_R1.fastq.gz -p trimmed_R2.fastq.gz sample_R1.fastq.gz sample_R2.fastq.gz
        """
        return trim_adapters_response
    
    def run_upstream_workflow_align_bowtie2(self):
        """Run Bowtie2 alignment workflow"""
        logger.info("Running Bowtie2 alignment workflow")
        align_bowtie2_response = f"""
# ATAC-seq Alignment with Bowtie2

# Paired-end alignment (recommended for ATAC-seq)
bowtie2 -x genomes/hg38_bowtie2 -1 sample_R1_val_1.fq.gz -2 sample_R2_val_2.fq.gz -p 4 --very-sensitive --dovetail --no-mixed --no-discordant -I 10 -X 700 | samtools view -bS - > sample.bam

# Single-end alignment
bowtie2 -x genomes/hg38_bowtie2 -U sample_trimmed.fq.gz -p 4 --very-sensitive | samtools view -bS - > sample.bam

# Sort and index BAM
samtools sort sample.bam -o sample_sorted.bam -@ 4
samtools index sample_sorted.bam

# Get alignment statistics
samtools flagstat sample_sorted.bam > sample_alignment_stats.txt
        """
        return align_bowtie2_response
    
    def run_upstream_workflow_align_bwa(self):
        """Run BWA alignment workflow"""
        logger.info("Running BWA alignment workflow")
        align_bwa_response = f"""
# ATAC-seq Alignment with BWA-MEM

# Paired-end alignment
bwa mem -t 4 genomes/hg38.fa sample_R1_val_1.fq.gz sample_R2_val_2.fq.gz | samtools view -bS - > sample.bam

# Single-end alignment
bwa mem -t 4 genomes/hg38.fa sample_trimmed.fq.gz | samtools view -bS - > sample.bam

# Sort and index BAM
samtools sort sample.bam -o sample_sorted.bam -@ 4
samtools index sample_sorted.bam

# Get alignment statistics
samtools flagstat sample_sorted.bam > sample_alignment_stats.txt
        """
        return align_bwa_response
    
    def run_upstream_workflow_filter_bam(self):
        """Run BAM filtering workflow"""
        logger.info("Running BAM filtering workflow")
        filter_bam_response = f"""
# Filter BAM Files for ATAC-seq

# Remove unmapped, low quality, and mitochondrial reads
samtools view -b -q 30 -F 4 sample_sorted.bam | grep -v chrM | samtools view -b - > sample_filtered.bam

# Alternative with more specific filtering
samtools view -b -f 2 -q 30 sample_sorted.bam chr1 chr2 chr3 chr4 chr5 chr6 chr7 chr8 chr9 chr10 chr11 chr12 chr13 chr14 chr15 chr16 chr17 chr18 chr19 chr20 chr21 chr22 chrX chrY > sample_filtered.bam

# Remove blacklist regions
bedtools intersect -v -a sample_filtered.bam -b annotations/hg38-blacklist.v2.bed > sample_filtered_clean.bam

# Sort and index filtered BAM
samtools sort sample_filtered_clean.bam -o sample_filtered_sorted.bam
samtools index sample_filtered_sorted.bam
        """
        return filter_bam_response
    
    def run_upstream_workflow_mark_duplicates(self):
        """Run duplicate marking workflow"""
        logger.info("Running duplicate marking workflow")
        mark_duplicates_response = f"""
# Mark/Remove PCR Duplicates

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
        """
        return mark_duplicates_response
    
    def run_upstream_workflow_process_bam_smart(self):
        """Run smart BAM processing workflow"""
        logger.info("Running smart BAM processing workflow")
        process_bam_smart_response = f"""
# Smart BAM Processing Pipeline

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
samtools view -f 2 sample_final.bam | awk '{{print $9}}' | awk '$1>0' | sort -n | uniq -c > fragment_sizes.txt
        """
        return process_bam_smart_response