"""ATAC-seq Downstream Analysis - Peak calling, visualization, and reporting"""

from pathlib import Path
from typing import Dict, Any
from ....utils.log import logger
from ....toolset import ToolSet, tool
from rich.console import Console

class ATACSeqAnalysisToolSet(ToolSet):
    """ATAC-seq Downstream Analysis Toolset - Peak calling to final reports"""
    
    def __init__(
        self,
        name: str = "atac_analysis",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.console = Console()

        
    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize ATAC-seq pipeline configuration for downstream analysis"""
        return {
            "file_extensions": {
                "alignment": [".sam", ".bam", ".cram", ".bam.bai", ".cram.crai"],
                "peaks": [".narrowPeak", ".broadPeak", ".gappedPeak", ".xls", ".bedgraph", ".bdg"],
                "tracks": [".bw", ".bigwig", ".tdf"],
                "reports": [".html", ".json", ".txt", ".tsv", ".csv", ".pdf", ".png"]
            },
            "tools": {
                "peak_calling": ["macs2", "genrich", "hmmratac"],
                "coverage": ["deeptools", "bedtools", "ucsc-tools"],
                "annotation": ["homer", "meme", "chipseeker", "bedtools"],
                "qc": ["multiqc"]
            },
            "default_params": {
                "threads": 4,
                "memory": "8G",
                "peak_calling_fdr": 0.01
            }
        }

    @tool
    def ATAC_Analysis(self, workflow_type: str, description: str = None):
        """Run a specific ATAC-seq workflow"""
        if workflow_type == "call_peaks_macs2":
            return self.run_workflow_call_peaks_macs2()
        elif workflow_type == "call_peaks_genrich":
            return self.run_workflow_call_peaks_genrich()
        elif workflow_type == "bam_to_bigwig":
            return self.run_workflow_bam_to_bigwig()
        elif workflow_type == "compute_matrix":
            return self.run_workflow_compute_matrix()
        elif workflow_type == "plot_heatmap":
            return self.run_workflow_plot_heatmap()
        elif workflow_type == "find_motifs":
            return self.run_workflow_find_motifs()
        elif workflow_type == "generate_atac_qc_report":
            return self.run_workflow_generate_atac_qc_report()
        elif workflow_type == "run_full_pipeline":
            return self.run_workflow_run_full_pipeline()
        else:
            return "Invalid workflow type"
    
    def run_workflow_call_peaks_macs2(self):
        """Run MACS2 peak calling workflow"""
        logger.info("Running MACS2 peak calling workflow")
        call_peaks_macs2_response = f"""
# ATAC-seq Peak Calling with MACS2

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
        """
        return call_peaks_macs2_response
    
    def run_workflow_call_peaks_genrich(self):
        """Run Genrich peak calling workflow"""
        logger.info("Running Genrich peak calling workflow")
        call_peaks_genrich_response = f"""
# ATAC-seq Peak Calling with Genrich (ATAC-seq optimized)

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
        """
        return call_peaks_genrich_response
    
    def run_workflow_bam_to_bigwig(self):
        """Run BAM to BigWig conversion workflow"""
        logger.info("Running BAM to BigWig conversion workflow")
        bam_to_bigwig_response = f"""
# Convert BAM to BigWig using deepTools

# Basic conversion with RPKM normalization
bamCoverage -b input.bam -o output.bw --normalizeUsing RPKM --binSize 10 -p 4

# With CPM normalization
bamCoverage -b input.bam -o output.bw --normalizeUsing CPM --binSize 10 -p 4

# High resolution (smaller bin size)
bamCoverage -b input.bam -o output.bw --normalizeUsing RPKM --binSize 5 -p 4

# For paired-end data
bamCoverage -b input.bam -o output.bw --normalizeUsing RPKM --binSize 10 -p 4 --extendReads

# Filter by quality
bamCoverage -b input.bam -o output.bw --normalizeUsing RPKM --binSize 10 -p 4 --minMappingQuality 30
        """
        return bam_to_bigwig_response
    
    def run_workflow_compute_matrix(self):
        """Run compute matrix workflow"""
        logger.info("Running compute matrix workflow")
        compute_matrix_response = f"""
# Compute matrix for deepTools plots

# Reference-point mode (around peak centers)
computeMatrix reference-point -S sample.bw -R peaks.bed -o matrix.mat.gz -a 3000 -b 3000 -p 4

# Scale-regions mode (scale peaks to same size)
computeMatrix scale-regions -S sample.bw -R peaks.bed -o matrix.mat.gz -m 2000 -a 3000 -b 3000 -p 4

# Multiple BigWig files
computeMatrix reference-point -S sample1.bw sample2.bw -R peaks.bed -o matrix.mat.gz -a 3000 -b 3000 -p 4

# Multiple region files
computeMatrix reference-point -S sample.bw -R peaks.bed genes.bed -o matrix.mat.gz -a 3000 -b 3000 -p 4
        """
        return compute_matrix_response
    
    def run_workflow_plot_heatmap(self):
        """Run plot heatmap workflow"""
        logger.info("Running plot heatmap workflow")
        plot_heatmap_response = f"""
# Plot heatmap from matrix using deepTools

# Basic heatmap
plotHeatmap -m matrix.mat.gz -o heatmap.png --colorMap RdBu_r --whatToShow "heatmap and colorbar"

# With different colormap
plotHeatmap -m matrix.mat.gz -o heatmap.png --colorMap viridis --whatToShow "heatmap and colorbar"

# Advanced heatmap with clustering
plotHeatmap -m matrix.mat.gz -o heatmap.png --colorMap RdBu_r --whatToShow "heatmap and colorbar" --kmeans 3

# Profile plot instead of heatmap
plotProfile -m matrix.mat.gz -o profile.png --colors red blue
        """
        return plot_heatmap_response
    
    def run_workflow_find_motifs(self):
        """Run motif finding workflow"""
        logger.info("Running motif finding workflow")
        find_motifs_response = f"""
# Find enriched motifs using HOMER

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
        """
        return find_motifs_response
    
    def run_workflow_generate_atac_qc_report(self):
        """Run ATAC-seq QC report generation workflow"""
        logger.info("Running ATAC-seq QC report generation workflow")
        generate_atac_qc_response = f"""
# Generate comprehensive ATAC-seq QC report

# Basic alignment statistics
samtools flagstat sample.bam > sample_flagstat.txt

# Count peaks
wc -l peaks.narrowPeak

# Calculate FRiP (Fraction of Reads in Peaks)
bedtools intersect -a sample.bam -b peaks.narrowPeak -c | awk '{{sum+=$NF}} END {{print sum}}'

# Fragment size distribution
samtools view -f 2 sample.bam | awk '{{print $9}}' | awk '$1>0' | sort -n | uniq -c > fragment_sizes.txt

# TSS enrichment using deepTools
computeMatrix reference-point -S sample.bw -R tss.bed -o tss_matrix.mat.gz -a 2000 -b 2000 -p 4
plotProfile -m tss_matrix.mat.gz -o tss_enrichment.png

# Generate MultiQC report
multiqc --outdir reports/ --filename multiqc_report.html .
        """
        return generate_atac_qc_response
    
    def run_workflow_run_full_pipeline(self):
        """Run full ATAC-seq pipeline workflow"""
        logger.info("Running full ATAC-seq pipeline workflow")
        run_full_pipeline_response = f"""
# Complete ATAC-seq Pipeline from FASTQ to Peaks

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
        """
        return run_full_pipeline_response