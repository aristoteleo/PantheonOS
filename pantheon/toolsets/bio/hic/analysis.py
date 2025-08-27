"""Hi-C Analysis - Downstream analysis including TAD calling, compartment analysis, and visualization"""

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

class HiCAnalysisToolSet(ToolSet):
    """Hi-C Analysis Toolset - TAD calling, compartment analysis, and visualization"""
    
    def __init__(
        self,
        name: str = "hic_analysis",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.analysis_config = self._initialize_config()
        self.console = Console()
        
    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize Hi-C analysis configuration"""
        return {
            "file_extensions": {
                "hic_matrix": [".h5", ".cool", ".mcool", ".hic"],
                "tads": [".bed", ".domains", ".boundaries"],
                "compartments": [".bed", ".pc1.bedgraph", ".eigen.bedgraph"],
                "loops": [".bedpe", ".hichip", ".loops"],
                "tracks": [".bw", ".bigwig", ".bedgraph"],
                "plots": [".png", ".pdf", ".svg"],
                "tables": [".tsv", ".csv", ".txt"]
            },
            "tools": {
                "tad_calling": ["hicexplorer", "tadtool", "armatus", "insulation"],
                "compartment_analysis": ["hicexplorer", "cooltools", "fanc"],
                "loop_calling": ["hiccups", "chromosight", "mustache"],
                "visualization": ["hicexplorer", "pygenometracks", "cooler", "juicebox"],
                "differential": ["hiccompare", "multicomp", "diffhic"],
                "integration": ["epic2", "chipseeker", "genomicranges"]
            },
            "default_params": {
                "tad_resolution": 50000,
                "compartment_resolution": 100000, 
                "loop_resolution": 10000,
                "min_tad_size": 100000,
                "max_tad_size": 2000000,
                "pvalue_threshold": 0.05,
                "fdr_threshold": 0.1
            }
        }
    
    @tool 
    def HiC_Analysis(self, workflow_type: str, description: str = None):
        """Run a specific Hi-C analysis workflow"""
        if workflow_type == "call_tads":
            return self.run_analysis_workflow_call_tads()
        elif workflow_type == "find_compartments":
            return self.run_analysis_workflow_find_compartments()
        elif workflow_type == "call_loops":
            return self.run_analysis_workflow_call_loops()
        elif workflow_type == "plot_matrix":
            return self.run_analysis_workflow_plot_matrix()
        elif workflow_type == "plot_tads":
            return self.run_analysis_workflow_plot_tads()
        elif workflow_type == "differential_analysis":
            return self.run_analysis_workflow_differential_analysis()
        elif workflow_type == "integration_analysis":
            return self.run_analysis_workflow_integration_analysis()
        elif workflow_type == "generate_tracks":
            return self.run_analysis_workflow_generate_tracks()
        else:
            return "Invalid workflow type"
    
    def run_analysis_workflow_call_tads(self):
        """Run TAD calling workflow"""
        logger.info("Running Hi-C TAD calling workflow")
        call_tads_response = f"""
# Call Topologically Associating Domains (TADs)

# Find TADs using HiCExplorer
for sample in $(find matrices/corrected/ -name "*_50000_corrected.h5" | xargs -n1 basename | sed 's/_50000_corrected.h5//g'); do
    echo "Calling TADs for sample: $sample"
    
    # Call TADs using the insulation score method
    hicFindTADs \\
        --matrix matrices/corrected/${{sample}}_50000_corrected.h5 \\
        --outPrefix tads/${{sample}}_tads \\
        --correctForMultipleTesting fdr \\
        --threshold 0.05 \\
        --delta 0.01 \\
        --minDepth 20000 \\
        --maxDepth 200000
        
    # Method 2: Alternative insulation score parameters
    hicFindTADs \\
        --matrix matrices/corrected/${{sample}}_50000_corrected.h5 \\
        --outPrefix tads/${{sample}}_insulation \\
        --minBoundaryDistance 100000 \\
        --maxBoundaryDistance 2000000 \\
        --step 10000 \\
        --thresholdComparisons 0.01
done

# Merge TADs from multiple samples
for sample in $(ls tads/*_tads_domains.bed | sed 's/_tads_domains.bed//g' | xargs -n1 basename); do
    echo "Processing TADs for: $sample"
    
    # Convert to standard BED format
    awk 'OFS="\\t" {{print $1, $2, $3, "TAD_"NR, ".", "."}}' tads/${{sample}}_domains.bed > tads/${{sample}}_tads_final.bed
    
    # Get TAD boundaries
    awk 'OFS="\\t" {{print $1, $3-5000, $3+5000, "Boundary_"NR, ".", "."}}' tads/${{sample}}_domains.bed > tads/${{sample}}_boundaries.bed
done

echo "TAD calling complete! Check tads/ folder for domain and boundary files."
        """
        return call_tads_response
    
    def run_analysis_workflow_find_compartments(self):
        """Run compartment analysis workflow"""
        logger.info("Running Hi-C compartment analysis workflow")
        find_compartments_response = f"""
# Find A/B Compartments using PCA

# Perform PCA analysis to identify compartments
for sample in $(find matrices/corrected/ -name "*_100000_corrected.h5" | xargs -n1 basename | sed 's/_100000_corrected.h5//g'); do
    echo "Finding compartments for sample: $sample"
    
    # Run PCA to get PC1 (compartments)
    hicPCA \\
        --matrix matrices/corrected/${{sample}}_100000_corrected.h5 \\
        --outputFileName compartments/${{sample}}_pc1.bedgraph compartments/${{sample}}_pc2.bedgraph \\
        --format bedgraph \\
        --pearsonMatrix compartments/${{sample}}_pearson.h5
        
    # Convert bedgraph to bigwig for genome browsers
    bedGraphToBigWig compartments/${{sample}}_pc1.bedgraph references/genome/hg38.chrom.sizes compartments/${{sample}}_pc1.bw
    bedGraphToBigWig compartments/${{sample}}_pc2.bedgraph references/genome/hg38.chrom.sizes compartments/${{sample}}_pc2.bw
    
    # Define A/B compartments based on PC1 values and gene density
    # Positive PC1 values (A compartments) - gene-rich, active
    # Negative PC1 values (B compartments) - gene-poor, inactive
    awk '$4 > 0 {{print $1, $2, $3, "A_compartment", $4, "+"}}' OFS="\\t" compartments/${{sample}}_pc1.bedgraph > compartments/${{sample}}_A_compartments.bed
    awk '$4 < 0 {{print $1, $2, $3, "B_compartment", $4, "-"}}' OFS="\\t" compartments/${{sample}}_pc1.bedgraph > compartments/${{sample}}_B_compartments.bed
    
    # Calculate compartment statistics
    echo "Compartment statistics for $sample:" > compartments/${{sample}}_compartment_stats.txt
    echo "A compartments: $(wc -l < compartments/${{sample}}_A_compartments.bed)" >> compartments/${{sample}}_compartment_stats.txt
    echo "B compartments: $(wc -l < compartments/${{sample}}_B_compartments.bed)" >> compartments/${{sample}}_compartment_stats.txt
done

echo "Compartment analysis complete! Check compartments/ folder for PC1/PC2 tracks and A/B compartment regions."
        """
        return find_compartments_response
    
    def run_analysis_workflow_call_loops(self):
        """Run loop calling workflow"""  
        logger.info("Running Hi-C loop calling workflow")
        call_loops_response = f"""
# Call Chromatin Loops

# Detect significant chromatin loops
for sample in $(find matrices/corrected/ -name "*_10000_corrected.h5" | xargs -n1 basename | sed 's/_10000_corrected.h5//g'); do
    echo "Calling loops for sample: $sample"
    
    # Use HiCExplorer to detect loops
    hicDetectLoops \\
        --matrix matrices/corrected/${{sample}}_10000_corrected.h5 \\
        --outFileName loops/${{sample}}_loops.bedpe \\
        --windowSize 10 \\
        --peakWidth 6 \\
        --pValuePreselection 0.05 \\
        --pValue 0.05 \\
        --threads 8
        
    # Filter high-confidence loops
    awk '$7 <= 0.01' loops/${{sample}}_loops.bedpe > loops/${{sample}}_loops_highconf.bedpe
    
    # Convert to UCSC interaction format
    awk 'OFS="\\t" {{print $1":"$2"-"$3, $4":"$5"-"$6, $7}}' loops/${{sample}}_loops_highconf.bedpe > loops/${{sample}}_loops_interact.txt
    
    # Generate loop statistics
    echo "Loop statistics for $sample:" > loops/${{sample}}_loop_stats.txt
    echo "Total loops detected: $(wc -l < loops/${{sample}}_loops.bedpe)" >> loops/${{sample}}_loop_stats.txt
    echo "High-confidence loops (p<0.01): $(wc -l < loops/${{sample}}_loops_highconf.bedpe)" >> loops/${{sample}}_loop_stats.txt
    
    # Get distance distribution of loops
    awk 'OFS="\\t" {{dist = ($5+$6)/2 - ($2+$3)/2; if (dist > 0) print dist}}' loops/${{sample}}_loops_highconf.bedpe | \\
        sort -n > loops/${{sample}}_loop_distances.txt
done

echo "Loop calling complete! Check loops/ folder for detected chromatin loops."
        """
        return call_loops_response
    
    def run_analysis_workflow_plot_matrix(self):
        """Run Hi-C matrix plotting workflow"""
        logger.info("Running Hi-C matrix plotting workflow")
        plot_matrix_response = f"""
# Plot Hi-C Contact Matrices

# Generate Hi-C matrix plots at different resolutions
for sample in $(find matrices/corrected/ -name "*_corrected.h5" | sed 's/_corrected.h5//g' | xargs -n1 basename); do
    echo "Plotting matrix for: $sample"
    
    # Extract resolution from filename
    resolution=$(echo $sample | grep -o '[0-9]\\+' | tail -1)
    base_sample=$(echo $sample | sed 's/_[0-9]\\+//')
    
    # Plot whole genome matrix
    hicPlotMatrix \\
        --matrix matrices/corrected/${{sample}}_corrected.h5 \\
        --outFileName visualization/${{base_sample}}_${{resolution}}_matrix.png \\
        --title "$base_sample Hi-C Matrix (${{resolution}}bp)" \\
        --colorMap RdYlBu_r \\
        --log1p \\
        --dpi 300
        
    # Plot chromosome-specific matrices
    for chr in chr1 chr2 chr3 chrX; do
        hicPlotMatrix \\
            --matrix matrices/corrected/${{sample}}_corrected.h5 \\
            --outFileName visualization/${{base_sample}}_${{resolution}}_${{chr}}_matrix.png \\
            --title "$base_sample $chr Hi-C Matrix (${{resolution}}bp)" \\
            --colorMap RdYlBu_r \\
            --log1p \\
            --region $chr \\
            --dpi 300
    done
done

# Generate correlation plots between samples
hicCorrelate \\
    --matrices matrices/corrected/*_100000_corrected.h5 \\
    --outFileNameHeatmap visualization/sample_correlation_heatmap.png \\
    --outFileNameScatter visualization/sample_correlation_scatter.png \\
    --method pearson \\
    --plotNumbers

echo "Hi-C matrix plotting complete! Check visualization/ folder for matrix plots."
        """
        return plot_matrix_response
    
    def run_analysis_workflow_plot_tads(self):
        """Run TAD plotting workflow"""
        logger.info("Running Hi-C TAD plotting workflow")
        plot_tads_response = f"""
# Plot TADs with PyGenomeTracks

# Create track configuration files for TAD visualization
for sample in $(ls tads/*_tads_final.bed | sed 's/_tads_final.bed//g' | xargs -n1 basename); do
    echo "Creating TAD plots for: $sample"
    
    # Create configuration file
    cat > visualization/${{sample}}_tad_tracks.ini << EOF
[spacer]
height = 0.5

[Hi-C Matrix]
file = matrices/corrected/${{sample}}_50000_corrected.h5
title = $sample Hi-C Matrix
colormap = RdYlBu_r
depth = 2000000
transform = log1p
file_type = hic_matrix

[TAD Boundaries]
file = tads/${{sample}}_boundaries.bed
color = black
height = 0.5
title = TAD Boundaries
file_type = bed

[TAD Domains]
file = tads/${{sample}}_tads_final.bed
color = blue
alpha = 0.5
height = 1
title = TADs
file_type = bed

[Compartments PC1]
file = compartments/${{sample}}_pc1.bedgraph
color = red
height = 1.5
title = A/B Compartments (PC1)
file_type = bedgraph

[spacer]
height = 0.5
EOF

    # Generate TAD plots for specific regions
    for region in "chr1:1000000-5000000" "chr2:1000000-5000000" "chrX:1000000-5000000"; do
        region_name=$(echo $region | tr ':' '_' | tr '-' '_')
        
        pyGenomeTracks --tracks visualization/${{sample}}_tad_tracks.ini \\
            --region $region \\
            --outFileName visualization/${{sample}}_tads_${{region_name}}.png \\
            --title "$sample TADs - $region" \\
            --width 12 --height 8 --dpi 300
    done
done

echo "TAD plotting complete! Check visualization/ folder for TAD plots with Hi-C matrices."
        """
        return plot_tads_response
    
    def run_analysis_workflow_differential_analysis(self):
        """Run differential Hi-C analysis workflow"""
        logger.info("Running differential Hi-C analysis workflow") 
        differential_analysis_response = f"""
# Differential Hi-C Analysis

# Compare Hi-C matrices between conditions
echo "Running differential analysis between conditions..."

# Differential TAD analysis
hicDifferentialTAD \\
    --targetMatrix matrices/corrected/condition1_50000_corrected.h5 \\
    --controlMatrix matrices/corrected/condition2_50000_corrected.h5 \\
    --tadDomains tads/condition1_tads_final.bed tads/condition2_tads_final.bed \\
    --outFileNamePrefix differential/tad_differential \\
    --pValue 0.05 \\
    --threads 8

# Compare interaction frequencies
hicCompareMatrices \\
    --matrices matrices/corrected/condition1_100000_corrected.h5 matrices/corrected/condition2_100000_corrected.h5 \\
    --outFileName differential/interaction_differences.h5 \\
    --operation diff

# Plot differential interactions
hicPlotMatrix \\
    --matrix differential/interaction_differences.h5 \\
    --outFileName visualization/differential_interactions.png \\
    --title "Differential Hi-C Interactions" \\
    --colorMap RdBu_r \\
    --vMin -2 --vMax 2

# Analyze compartment switching
echo "Analyzing A/B compartment switching..."

# Compare PC1 values between conditions
python << 'EOF'
import pandas as pd
import numpy as np

# Load PC1 values
pc1_cond1 = pd.read_csv('compartments/condition1_pc1.bedgraph', sep='\\t', 
                       names=['chr', 'start', 'end', 'pc1_cond1'])
pc1_cond2 = pd.read_csv('compartments/condition2_pc1.bedgraph', sep='\\t', 
                       names=['chr', 'start', 'end', 'pc1_cond2'])

# Merge dataframes
merged = pd.merge(pc1_cond1, pc1_cond2, on=['chr', 'start', 'end'])

# Find compartment switches
switches = merged[
    ((merged['pc1_cond1'] > 0) & (merged['pc1_cond2'] < 0)) |  # A to B
    ((merged['pc1_cond1'] < 0) & (merged['pc1_cond2'] > 0))    # B to A
]

switches.to_csv('differential/compartment_switches.bed', sep='\\t', index=False)
print(f"Found {{len(switches)}} compartment switching regions")
EOF

echo "Differential Hi-C analysis complete! Check differential/ folder for results."
        """
        return differential_analysis_response
    
    def run_analysis_workflow_integration_analysis(self):
        """Run multi-omics integration analysis workflow"""
        logger.info("Running Hi-C integration analysis workflow")
        integration_analysis_response = f"""
# Multi-omics Integration with Hi-C

# Integrate ChIP-seq peaks with TADs
echo "Integrating ChIP-seq data with TADs..."

# Overlap ChIP-seq peaks with TAD boundaries
for chipseq_file in chipseq_data/*.bed; do
    peak_name=$(basename $chipseq_file .bed)
    
    bedtools intersect -a tads/sample_boundaries.bed -b $chipseq_file -wa -wb > \\
        integration/${{peak_name}}_tad_boundary_overlap.bed
    
    # Calculate enrichment at TAD boundaries
    bedtools intersect -a tads/sample_boundaries.bed -b $chipseq_file -c > \\
        integration/${{peak_name}}_boundary_counts.bed
done

# Integrate RNA-seq with compartments
echo "Integrating RNA-seq data with A/B compartments..."

# Overlap genes with compartments
bedtools intersect -a annotations/genes.bed -b compartments/sample_A_compartments.bed -wa > \\
    integration/genes_in_A_compartments.bed

bedtools intersect -a annotations/genes.bed -b compartments/sample_B_compartments.bed -wa > \\
    integration/genes_in_B_compartments.bed

# Calculate gene expression levels in A vs B compartments
python << 'EOF'
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Load expression data
expr = pd.read_csv('rnaseq_data/expression_matrix.tsv', sep='\\t', index_col=0)

# Load compartment annotations
a_genes = pd.read_csv('integration/genes_in_A_compartments.bed', sep='\\t', 
                     names=['chr', 'start', 'end', 'gene_id'], usecols=[3])
b_genes = pd.read_csv('integration/genes_in_B_compartments.bed', sep='\\t', 
                     names=['chr', 'start', 'end', 'gene_id'], usecols=[3])

# Get expression for A and B compartment genes
a_expr = expr[expr.index.isin(a_genes['gene_id'])].mean(axis=1)
b_expr = expr[expr.index.isin(b_genes['gene_id'])].mean(axis=1)

# Statistical test
from scipy import stats
t_stat, p_val = stats.ttest_ind(a_expr, b_expr)

print(f"A compartment mean expression: {{a_expr.mean():.2f}}")
print(f"B compartment mean expression: {{b_expr.mean():.2f}}")
print(f"T-test p-value: {{p_val:.2e}}")

# Plot expression comparison
plt.figure(figsize=(8, 6))
plt.boxplot([a_expr, b_expr], labels=['A Compartments', 'B Compartments'])
plt.ylabel('Log2(Expression + 1)')
plt.title('Gene Expression in A vs B Compartments')
plt.savefig('integration/compartment_expression_comparison.png', dpi=300)
EOF

# Analyze loop anchors for regulatory elements
echo "Analyzing chromatin loops and regulatory elements..."

# Intersect loop anchors with regulatory elements
awk 'OFS="\\t" {{print $1, $2, $3, "loop_anchor_1"; print $4, $5, $6, "loop_anchor_2"}}' \\
    loops/sample_loops_highconf.bedpe > integration/loop_anchors.bed

for regulatory_file in regulatory_data/*.bed; do
    reg_name=$(basename $regulatory_file .bed)
    
    bedtools intersect -a integration/loop_anchors.bed -b $regulatory_file -wa -wb > \\
        integration/loops_${{reg_name}}_overlap.bed
done

echo "Multi-omics integration analysis complete! Check integration/ folder for results."
        """
        return integration_analysis_response
    
    def run_analysis_workflow_generate_tracks(self):
        """Run genome browser track generation workflow"""
        logger.info("Running Hi-C track generation workflow")
        generate_tracks_response = f"""
# Generate Genome Browser Tracks

# Convert Hi-C matrices to various browser-compatible formats
echo "Converting Hi-C matrices to browser tracks..."

for sample in $(find matrices/corrected/ -name "*_corrected.h5" | sed 's/_corrected.h5//g' | xargs -n1 basename); do
    resolution=$(echo $sample | grep -o '[0-9]\\+' | tail -1)
    base_sample=$(echo $sample | sed 's/_[0-9]\\+//')
    
    echo "Processing $sample (resolution: ${{resolution}}bp)"
    
    # Convert to cool format (for HiGlass browser)
    hicConvertFormat -m matrices/corrected/${{sample}}_corrected.h5 \\
        --inputFormat h5 --outputFormat cool \\
        -o tracks/${{base_sample}}_${{resolution}}.cool
    
    # Create multi-resolution cool file
    cooler coarsen tracks/${{base_sample}}_${{resolution}}.cool \\
        -o tracks/${{base_sample}}_multi.mcool \\
        -k 2,4,8
    
    # Generate interaction bedgraph for UCSC browser
    hicExport -m matrices/corrected/${{sample}}_corrected.h5 \\
        --outputFormat bedgraph \\
        -o tracks/${{base_sample}}_${{resolution}}_interactions.bedgraph
    
    # Convert to BigWig
    sort -k1,1 -k2,2n tracks/${{base_sample}}_${{resolution}}_interactions.bedgraph > \\
        tracks/${{base_sample}}_${{resolution}}_sorted.bedgraph
    
    bedGraphToBigWig tracks/${{base_sample}}_${{resolution}}_sorted.bedgraph \\
        references/genome/hg38.chrom.sizes \\
        tracks/${{base_sample}}_${{resolution}}_interactions.bw
done

# Create track hub descriptor files
echo "Creating track hub files..."

cat > tracks/trackDb.txt << EOF
track hic_matrices
compositeTrack on
shortLabel Hi-C Matrices
longLabel Hi-C Contact Matrices
type bigWig
visibility dense

    track hic_sample1
    parent hic_matrices
    bigDataUrl sample1_100000_interactions.bw
    shortLabel Sample1 Hi-C
    longLabel Sample1 Hi-C Interactions (100kb)
    type bigWig
    color 255,0,0

    track compartments_pc1
    parent hic_matrices  
    bigDataUrl sample1_pc1.bw
    shortLabel PC1 Compartments
    longLabel A/B Compartments (PC1)
    type bigWig
    color 0,0,255

track tads
shortLabel TADs
longLabel Topologically Associating Domains
type bed
itemRgb on
bigDataUrl sample1_tads_final.bb

track loops
shortLabel Chromatin Loops
longLabel Significant Chromatin Loops
type interact
bigDataUrl sample1_loops_interact.bb
EOF

# Create UCSC session file
cat > tracks/session.txt << EOF
browser position chr1:1000000-5000000
browser hide all
track hic_matrices on
track tads pack
track loops pack
EOF

echo "Genome browser tracks generated! Check tracks/ folder for browser-compatible files."
echo "Upload tracks/ contents to a web server and load into genome browsers."
        """
        return generate_tracks_response