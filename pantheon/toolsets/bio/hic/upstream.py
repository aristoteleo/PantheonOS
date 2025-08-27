"""Hi-C Upstream Analysis - Enhanced data preparation, QC, alignment, and matrix generation"""

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

class HiCUpstreamToolSet(ToolSet):
    """Hi-C Upstream Analysis Toolset - From FASTQ to Hi-C contact matrix"""
    
    def __init__(
        self,
        name: str = "hic_upstream",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.pipeline_config = self._initialize_config()
        self.console = Console()
        
    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize Hi-C pipeline configuration"""
        return {
            "file_extensions": {
                "raw_reads": [".fastq", ".fq", ".fastq.gz", ".fq.gz", ".fastq.bz2", 
                             ".fq.bz2", ".fastq.zst", ".fq.zst", ".sra"],
                "genome": [".fa", ".fasta", ".fai", ".dict"],
                "bwa_index": [".amb", ".ann", ".bwt", ".pac", ".sa"],
                "restriction_sites": [".bed", ".txt"],
                "alignment": [".sam", ".bam", ".cram", ".bam.bai", ".cram.crai"],
                "hic_matrix": [".h5", ".cool", ".mcool", ".hic"],
                "pairs": [".pairs", ".pairs.gz", ".pairs.px2"],
                "tracks": [".bw", ".bigwig", ".bedgraph"],
                "reports": [".html", ".json", ".txt", ".tsv", ".csv", ".pdf", ".png"]
            },
            "tools": {
                "acquisition": ["sra-tools", "pigz", "pbzip2", "zstd"],
                "qc": ["fastqc", "multiqc", "trim_galore", "cutadapt", "fastp"],
                "alignment": ["bwa", "bwa-mem2", "bowtie2"],
                "hic_processing": ["hicexplorer", "cooler", "juicer", "hic-pro", "pairix"],
                "sam_processing": ["samtools", "sambamba", "picard"],
                "matrix_ops": ["cooler", "pairix", "pairs-tools", "hicstuff"],
                "visualization": ["hicexplorer", "pygenometracks", "cooler", "higlass"],
                "restriction": ["findRestSites", "digest_genome.py"]
            },
            "default_params": {
                "threads": 8,
                "memory": "32G",
                "quality_threshold": 10,
                "min_mapping_quality": 10,
                "resolutions": [5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000],
                "enzyme": "MboI",
                "enzyme_sequence": "GATC",
                "genome_build": "hg38"
            }
        }
        
    

    @tool
    def HiC_Upstream(self, workflow_type: str, description: str = None):
        """Run a specific Hi-C upstream workflow"""
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
        elif workflow_type == "align_reads":
            return self.run_upstream_workflow_align_reads()
        elif workflow_type == "process_bam":
            return self.run_upstream_workflow_process_bam()
        elif workflow_type == "build_matrix":
            return self.run_upstream_workflow_build_matrix()
        elif workflow_type == "correct_matrix":
            return self.run_upstream_workflow_correct_matrix()
        elif workflow_type == "generate_qc":
            return self.run_upstream_workflow_generate_qc()
        elif workflow_type == "find_restriction_sites":
            return self.run_upstream_workflow_find_restriction_sites()
        elif workflow_type == "validate_enzyme":
            return self.run_upstream_workflow_validate_enzyme()
        else:
            return "Invalid workflow type"
    
    def run_upstream_workflow_init(self):
        """Run project initialization workflow"""
        logger.info("Running Hi-C project initialization workflow")
        init_response = f"""
# Initialize Hi-C Analysis Project

# Create Hi-C project directory structure
mkdir -p hic_analysis/{{raw_data,trimmed_data,qc/{{fastqc,multiqc,hicqc}},alignment,matrices/{{raw,corrected,normalized}},tads,compartments,loops,visualization,reports,logs,scripts,references/{{genome,enzyme_sites,index/{{bwa,bowtie2}}}}}}

# Create config file
cat > hic_analysis/hic_config.json << EOF
{{
  "project_name": "hic_analysis",
  "genome": "hg38", 
  "enzyme": "MboI",
  "paired_end": true,
  "resolutions": [10000, 50000, 100000, 1000000],
  "created": "$(date)",
  "pipeline_version": "1.0.0"
}}
EOF

# Create sample sheet template for Hi-C
cat > hic_analysis/samples.tsv << EOF
sample_id\tfastq_r1\tfastq_r2\tcell_type\ttreatment\treplicate\tbatch\tenzyme\tnotes
# Hi-C Sample Examples:
# Ctrl_1\tctrl_1_R1.fastq.gz\tctrl_1_R2.fastq.gz\tGM12878\tcontrol\t1\tbatch1\tMboI\tHi-C library
# Treat_1\ttreat_1_R1.fastq.gz\ttreat_1_R2.fastq.gz\tGM12878\ttreated\t1\tbatch1\tMboI\tHi-C library
EOF

echo "Hi-C project structure created successfully!"
echo "Directory structure created with Hi-C specific folders."
echo ""
echo "Next steps:"
echo "  1. Place FASTQ files in raw_data/"
echo "  2. Update samples.tsv with your sample information"
echo "  3. Update hic_config.json with your genome and enzyme"
echo "  4. Run: /bio hic upstream ./"
        """
        return init_response
    
    def run_upstream_workflow_check_dependencies(self):
        """Run dependency check workflow"""
        logger.info("Running Hi-C dependency check workflow")
        check_dependencies_response = f"""
# Check Hi-C Tool Dependencies

# Check core tools
which fastqc || echo "Missing: fastqc - conda install -c bioconda fastqc -y"
which bwa || echo "Missing: bwa - conda install -c bioconda bwa -y"
which samtools || echo "Missing: samtools - conda install -c bioconda samtools -y"
which cooler || echo "Missing: cooler - conda install -c bioconda cooler -y"
which hicBuildMatrix || echo "Missing: hicexplorer - conda install -c bioconda hicexplorer -y"
which multiqc || echo "Missing: multiqc - conda install -c bioconda multiqc -y"
which trim_galore || echo "Missing: trim_galore - conda install -c bioconda trim-galore -y"
which pairix || echo "Missing: pairix - conda install -c bioconda pairix -y"

# Check versions
echo "Tool versions:"
fastqc --version 2>/dev/null | head -1
bwa 2>&1 | head -3 | tail -1
samtools --version 2>/dev/null | head -1
cooler --version 2>/dev/null
hicBuildMatrix --version 2>/dev/null | head -1
        """
        return check_dependencies_response
    
    def run_upstream_workflow_setup_genome_resources(self):
        """Run genome setup workflow"""
        logger.info("Running Hi-C genome setup workflow")
        setup_genome_response = f"""
# Setup Hi-C Genome Resources

# Download human genome (hg38)
wget -P references/genome/ https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz
gunzip references/genome/hg38.fa.gz

# Build BWA index
bwa index references/genome/hg38.fa

# Create samtools index
samtools faidx references/genome/hg38.fa

# Create chromosome sizes file
cut -f1,2 references/genome/hg38.fa.fai > references/genome/hg38.chrom.sizes

# Generate restriction enzyme sites (MboI: GATC)
findRestSites --fasta references/genome/hg38.fa --searchPattern GATC --outFile references/enzyme_sites/hg38_MboI_sites.bed

# Alternative: Download pre-computed enzyme sites
# wget -P references/enzyme_sites/ https://github.com/deeptools/HiCExplorer/raw/master/hicexplorer/data/dm3_MboI.bed

echo "Genome resources setup complete!"
        """
        return setup_genome_response
    
    def run_upstream_workflow_run_fastqc(self):
        """Run FastQC workflow"""
        logger.info("Running Hi-C FastQC workflow")
        fastqc_response = f"""
# Run FastQC Quality Control for Hi-C

# Hi-C paired-end reads
fastqc raw_data/*_R1.fastq.gz raw_data/*_R2.fastq.gz -o qc/fastqc/ -t 8

# Generate MultiQC summary report
multiqc qc/fastqc/ -o qc/multiqc/ -n "hic_raw_reads_qc"

echo "Hi-C raw reads QC complete!"
        """
        return fastqc_response
    
    def run_upstream_workflow_trim_adapters(self):
        """Run adapter trimming workflow"""
        logger.info("Running Hi-C adapter trimming workflow")
        trim_adapters_response = f"""
# Trim Adapters for Hi-C Data

# Hi-C specific trimming (often minimal trimming needed)
for sample in $(ls raw_data/*_R1.fastq.gz | sed 's/_R1.fastq.gz//g' | xargs -n1 basename); do
    echo "Processing sample: $sample"
    
    trim_galore --paired \\
        raw_data/${{sample}}_R1.fastq.gz \\
        raw_data/${{sample}}_R2.fastq.gz \\
        -o trimmed_data/ \\
        --fastqc \\
        --length 20 \\
        --quality 20 \\
        --cores 4
done

# Generate MultiQC report for trimmed data
multiqc trimmed_data/ -o qc/multiqc/ -n "hic_trimmed_reads_qc"
        """
        return trim_adapters_response
    
    def run_upstream_workflow_align_reads(self):
        """Run alignment workflow"""
        logger.info("Running Hi-C alignment workflow")
        align_reads_response = f"""
# Hi-C Alignment with BWA

# Hi-C specific alignment (no paired-end mode, individual read alignment)
for sample in $(ls trimmed_data/*_R1_val_1.fq.gz | sed 's/_R1_val_1.fq.gz//g' | xargs -n1 basename); do
    echo "Aligning sample: $sample"
    
    # Align R1 reads
    bwa mem -t 8 references/genome/hg38.fa trimmed_data/${{sample}}_R1_val_1.fq.gz | \\
        samtools view -bS - > alignment/${{sample}}_R1.bam
    
    # Align R2 reads  
    bwa mem -t 8 references/genome/hg38.fa trimmed_data/${{sample}}_R2_val_2.fq.gz | \\
        samtools view -bS - > alignment/${{sample}}_R2.bam
    
    # Sort alignments
    samtools sort alignment/${{sample}}_R1.bam -o alignment/${{sample}}_R1_sorted.bam -@ 8
    samtools sort alignment/${{sample}}_R2.bam -o alignment/${{sample}}_R2_sorted.bam -@ 8
    
    # Index BAM files
    samtools index alignment/${{sample}}_R1_sorted.bam
    samtools index alignment/${{sample}}_R2_sorted.bam
    
    # Get alignment statistics
    samtools flagstat alignment/${{sample}}_R1_sorted.bam > alignment/${{sample}}_R1_flagstat.txt
    samtools flagstat alignment/${{sample}}_R2_sorted.bam > alignment/${{sample}}_R2_flagstat.txt
done

echo "Hi-C alignment complete!"
        """
        return align_reads_response
    
    def run_upstream_workflow_process_bam(self):
        """Run BAM processing workflow"""
        logger.info("Running Hi-C BAM processing workflow")
        process_bam_response = f"""
# Process Hi-C BAM Files

# Quality filtering and duplicate removal
for sample in $(ls alignment/*_R1_sorted.bam | sed 's/_R1_sorted.bam//g' | xargs -n1 basename); do
    echo "Processing BAM for sample: $sample"
    
    # Filter for quality >= 10 and properly mapped reads
    samtools view -b -q 10 -F 4 alignment/${{sample}}_R1_sorted.bam > alignment/${{sample}}_R1_filtered.bam
    samtools view -b -q 10 -F 4 alignment/${{sample}}_R2_sorted.bam > alignment/${{sample}}_R2_filtered.bam
    
    # Mark duplicates (optional for Hi-C)
    picard MarkDuplicates \\
        INPUT=alignment/${{sample}}_R1_filtered.bam \\
        OUTPUT=alignment/${{sample}}_R1_dedup.bam \\
        METRICS_FILE=alignment/${{sample}}_R1_dup_metrics.txt \\
        REMOVE_DUPLICATES=false
    
    picard MarkDuplicates \\
        INPUT=alignment/${{sample}}_R2_filtered.bam \\
        OUTPUT=alignment/${{sample}}_R2_dedup.bam \\
        METRICS_FILE=alignment/${{sample}}_R2_dup_metrics.txt \\
        REMOVE_DUPLICATES=false
    
    # Index final BAM files
    samtools index alignment/${{sample}}_R1_dedup.bam
    samtools index alignment/${{sample}}_R2_dedup.bam
done

echo "Hi-C BAM processing complete!"
        """
        return process_bam_response
    
    def run_upstream_workflow_build_matrix(self):
        """Run Hi-C matrix building workflow"""
        logger.info("Running Hi-C matrix building workflow")
        build_matrix_response = f"""
# Build Hi-C Contact Matrix with HiCExplorer

# Build Hi-C matrix from aligned reads
for sample in $(ls alignment/*_R1_dedup.bam | sed 's/_R1_dedup.bam//g' | xargs -n1 basename); do
    echo "Building matrix for sample: $sample"
    
    # Build raw Hi-C matrix at multiple resolutions
    for resolution in 10000 50000 100000 1000000; do
        echo "Building matrix at ${{resolution}}bp resolution..."
        
        hicBuildMatrix \\
            --samFiles alignment/${{sample}}_R1_dedup.bam alignment/${{sample}}_R2_dedup.bam \\
            --binSize ${{resolution}} \\
            --restrictionSequence GATC \\
            --danglingSequence GATC \\
            --restrictionCutFile references/enzyme_sites/hg38_MboI_sites.bed \\
            --threads 8 \\
            --inputBufferSize 400000 \\
            --outFileName matrices/raw/${{sample}}_${{resolution}}.h5 \\
            --QCfolder qc/hicqc/${{sample}}_${{resolution}}_QC \\
            --outBam matrices/raw/${{sample}}_${{resolution}}.bam
    done
done

echo "Hi-C matrix building complete!"
echo "Check qc/hicqc/ folders for quality reports!"
        """
        return build_matrix_response
    
    def run_upstream_workflow_correct_matrix(self):
        """Run Hi-C matrix correction workflow"""
        logger.info("Running Hi-C matrix correction workflow")
        correct_matrix_response = f"""
# Correct Hi-C Matrix with HiCExplorer

# Apply matrix correction (ICE normalization)
for sample in $(find matrices/raw/ -name "*.h5" | xargs -n1 basename | sed 's/.h5//g'); do
    echo "Correcting matrix: $sample"
    
    # Correct matrix using ICE
    hicCorrectMatrix correct \\
        --matrix matrices/raw/${{sample}}.h5 \\
        --correctionMethod ICE \\
        --iterNum 500 \\
        --outFileName matrices/corrected/${{sample}}_corrected.h5 \\
        --filterThreshold -1.5 3 \\
        --perchr
done

# Generate diagnostic plots
for sample in $(find matrices/corrected/ -name "*_corrected.h5" | xargs -n1 basename | sed 's/_corrected.h5//g'); do
    echo "Generating diagnostic plots for: $sample"
    
    hicCorrectMatrix diagnostic_plot \\
        --matrix matrices/corrected/${{sample}}_corrected.h5 \\
        --plotName qc/hicqc/${{sample}}_diagnostic_plot.png
done

echo "Hi-C matrix correction complete!"
        """
        return correct_matrix_response
    
    def run_upstream_workflow_generate_qc(self):
        """Run Hi-C QC generation workflow"""
        logger.info("Running Hi-C QC generation workflow")
        generate_qc_response = f"""
# Generate Hi-C Quality Control Reports

# Generate correlation between Hi-C matrices
hicCorrelate \\
    --matrices matrices/corrected/*_corrected.h5 \\
    --outFileNameHeatmap qc/hicqc/correlation_heatmap.png \\
    --outFileNameScatter qc/hicqc/correlation_scatter.png \\
    --method pearson \\
    --plotNumbers

# Generate PCA plot
hicPCA \\
    --matrix matrices/corrected/sample_100000_corrected.h5 \\
    --outputFileName matrices/corrected/sample_pca1.bigwig matrices/corrected/sample_pca2.bigwig \\
    --format bigwig

# Plot Hi-C matrix
hicPlotMatrix \\
    --matrix matrices/corrected/sample_100000_corrected.h5 \\
    --outFileName visualization/sample_matrix_plot.png \\
    --title "Hi-C Contact Matrix" \\
    --colorMap RdYlBu_r \\
    --log1p

# Generate comprehensive MultiQC report
multiqc qc/ alignment/ matrices/ -o reports/ -n "hic_comprehensive_qc"

echo "Hi-C QC generation complete!"
echo "Check reports/ folder for comprehensive QC report!"
        """
        return generate_qc_response
    
    def run_upstream_workflow_find_restriction_sites(self):
        """Run restriction enzyme site finding workflow"""
        logger.info("Running Hi-C restriction enzyme site finding workflow")
        find_restriction_sites_response = f"""
# Find Restriction Enzyme Sites for Hi-C Analysis

# Find restriction sites using HiCExplorer (required for hicBuildMatrix v3.6+)
for genome_file in references/genome/*.fa; do
    genome_name=$(basename $genome_file .fa)
    echo "Finding restriction sites for: $genome_name"
    
    # Find MboI sites (GATC)
    hicFindRestSites \\
        --fasta $genome_file \\
        --searchPattern GATC \\
        --outFile references/enzyme_sites/${{genome_name}}_MboI_sites.bed
    
    # Find DpnII sites (also GATC - same as MboI)
    cp references/enzyme_sites/${{genome_name}}_MboI_sites.bed references/enzyme_sites/${{genome_name}}_DpnII_sites.bed
    
    # Find HindIII sites (AAGCTT)
    hicFindRestSites \\
        --fasta $genome_file \\
        --searchPattern AAGCTT \\
        --outFile references/enzyme_sites/${{genome_name}}_HindIII_sites.bed
    
    # Find BglII sites (AGATCT)
    hicFindRestSites \\
        --fasta $genome_file \\
        --searchPattern AGATCT \\
        --outFile references/enzyme_sites/${{genome_name}}_BglII_sites.bed
        
    # Generate statistics for each enzyme
    for enzyme in MboI DpnII HindIII BglII; do
        site_file="references/enzyme_sites/${{genome_name}}_${{enzyme}}_sites.bed"
        if [ -f "$site_file" ]; then
            echo "$enzyme sites in $genome_name: $(wc -l < $site_file)" >> references/enzyme_sites/${{genome_name}}_enzyme_stats.txt
            
            # Calculate average fragment size
            awk 'BEGIN{{sum=0; count=0}} 
                 NR>1{{size=$2-prev_end; if(size>0 && size<10000000) {{sum+=size; count++}}}} 
                 {{prev_end=$3}} 
                 END{{if(count>0) print "Average fragment size: " sum/count " bp"}}' \\
                 $site_file >> references/enzyme_sites/${{genome_name}}_enzyme_stats.txt
        fi
    done
done

echo "Restriction enzyme site finding complete!"
echo "Check references/enzyme_sites/ for BED files and statistics."
        """
        return find_restriction_sites_response
    
    def run_upstream_workflow_validate_enzyme(self):
        """Run enzyme validation workflow"""
        logger.info("Running Hi-C enzyme validation workflow")
        validate_enzyme_response = f"""
# Validate Restriction Enzyme Choice for Hi-C Library

# Analyze FASTQ files to detect enzyme signatures
echo "Analyzing Hi-C library for enzyme signatures..."

for sample in $(ls raw_data/*_R1.fastq.gz | sed 's/_R1.fastq.gz//g' | xargs -n1 basename); do
    echo "Analyzing sample: $sample"
    
    # Extract first 100,000 reads for analysis
    zcat raw_data/${{sample}}_R1.fastq.gz | head -400000 > temp_R1_subset.fastq
    zcat raw_data/${{sample}}_R2.fastq.gz | head -400000 > temp_R2_subset.fastq
    
    # Look for restriction enzyme sequences at read ends
    echo "Enzyme signature analysis for $sample:" > qc/${{sample}}_enzyme_validation.txt
    
    # Check for MboI/DpnII (GATC) signatures
    gatc_count_r1=$(grep -c "^GATC" temp_R1_subset.fastq || echo "0")
    gatc_count_r2=$(grep -c "^GATC" temp_R2_subset.fastq || echo "0")
    echo "GATC at read start (MboI/DpnII): R1=$gatc_count_r1, R2=$gatc_count_r2" >> qc/${{sample}}_enzyme_validation.txt
    
    # Check for HindIII (AAGCTT) signatures
    hindiii_count_r1=$(grep -c "^AAGCTT" temp_R1_subset.fastq || echo "0")
    hindiii_count_r2=$(grep -c "^AAGCTT" temp_R2_subset.fastq || echo "0")
    echo "AAGCTT at read start (HindIII): R1=$hindiii_count_r1, R2=$hindiii_count_r2" >> qc/${{sample}}_enzyme_validation.txt
    
    # Check for BglII (AGATCT) signatures
    bglii_count_r1=$(grep -c "^AGATCT" temp_R1_subset.fastq || echo "0")
    bglii_count_r2=$(grep -c "^AGATCT" temp_R2_subset.fastq || echo "0")
    echo "AGATCT at read start (BglII): R1=$bglii_count_r1, R2=$bglii_count_r2" >> qc/${{sample}}_enzyme_validation.txt
    
    # Analyze fragment size distribution (from read names if available)
    echo "Fragment size analysis:" >> qc/${{sample}}_enzyme_validation.txt
    grep "^@" temp_R1_subset.fastq | head -1000 | \\
        grep -o "length=[0-9]*" | cut -d= -f2 | \\
        awk '{{sum+=$1; count++}} END{{if(count>0) print "Average fragment length: " sum/count " bp"}}' >> qc/${{sample}}_enzyme_validation.txt
    
    # Clean up temp files
    rm -f temp_R1_subset.fastq temp_R2_subset.fastq
    
    # Provide enzyme recommendation
    echo "" >> qc/${{sample}}_enzyme_validation.txt
    echo "Enzyme recommendation based on signatures:" >> qc/${{sample}}_enzyme_validation.txt
    
    if [ $gatc_count_r1 -gt $hindiii_count_r1 ] && [ $gatc_count_r1 -gt $bglii_count_r1 ]; then
        echo "Recommended enzyme: MboI or DpnII (GATC sequence detected)" >> qc/${{sample}}_enzyme_validation.txt
    elif [ $hindiii_count_r1 -gt $gatc_count_r1 ] && [ $hindiii_count_r1 -gt $bglii_count_r1 ]; then
        echo "Recommended enzyme: HindIII (AAGCTT sequence detected)" >> qc/${{sample}}_enzyme_validation.txt
    elif [ $bglii_count_r1 -gt $gatc_count_r1 ] && [ $bglii_count_r1 -gt $hindiii_count_r1 ]; then
        echo "Recommended enzyme: BglII (AGATCT sequence detected)" >> qc/${{sample}}_enzyme_validation.txt
    else
        echo "Recommended enzyme: MboI (default - most common for Hi-C)" >> qc/${{sample}}_enzyme_validation.txt
    fi
done

echo "Enzyme validation complete! Check qc/*_enzyme_validation.txt for recommendations."
        """
        return validate_enzyme_response