"""Single-cell ATAC-seq upstream processing with cellranger-atac"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from .base import ScATACSeqBase
from ...utils.toolset import tool
from ...utils.log import logger


class ScATACSeqUpstreamToolSet(ScATACSeqBase):
    """Single-cell ATAC-seq upstream processing toolset using cellranger-atac"""
    
    def __init__(
        self,
        name: str = "scatac_upstream",
        workspace_path: str = None,
        launch_directory: str = None,
        worker_params: dict = None,
        **kwargs
    ):
        super().__init__(name, workspace_path, launch_directory, worker_params, **kwargs)

    @tool
    def ScATAC_Upstream(self, workflow_type: str, description: str = None):
        """Run a specific scATAC-seq upstream workflow"""
        if workflow_type == "init":
            return self.run_upstream_workflow_init()
        elif workflow_type == "check_dependencies":
            return self.run_upstream_workflow_check_dependencies()
        elif workflow_type == "install_cellranger":
            return self.run_upstream_workflow_install_cellranger()
        elif workflow_type == "setup_reference":
            return self.run_upstream_workflow_setup_reference()
        elif workflow_type == "scan_folder":
            return self.run_upstream_workflow_scan_folder()
        elif workflow_type == "run_count":
            return self.run_upstream_workflow_run_count()
        elif workflow_type == "test_functionality":
            return self.run_upstream_workflow_test_functionality()
        else:
            return "Invalid workflow type"
    
    def run_upstream_workflow_init(self):
        """Run scATAC project initialization workflow"""
        logger.info("Running scATAC project initialization workflow")
        init_response = f"""
# Initialize Single-cell ATAC-seq Analysis Project

# Create project directory structure
mkdir -p scatac_analysis/{{raw_data,references,cellranger,filtered,analysis/{{embeddings,clustering,peak_annotation}},plots,reports,logs,scripts}}

# Create config file
cat > scatac_analysis/scatac_config.json << EOF
{{
  "project_name": "scatac_analysis",
  "project_type": "single_cell_atac_seq",
  "expected_cells": 10000,
  "created": "$(date -Iseconds)",
  "pipeline_version": "1.0.0",
  "parameters": {{
    "threads": 4,
    "memory": "64G",
    "min_cells": 3,
    "min_peaks": 200,
    "max_peaks": 100000,
    "mito_threshold": 20.0
  }}
}}
EOF

# Create sample sheet template
cat > scatac_analysis/samples.csv << EOF
sample_id,fastqs_path,expected_cells,description
# Example:
# Sample1,/path/to/fastqs,10000,Control sample
# Sample2,/path/to/fastqs,8000,Treatment sample
EOF

# Create README
cat > scatac_analysis/README.md << EOF
# scATAC-seq Analysis Project

Created on $(date)

## Project Structure
- raw_data/: Input FASTQ files
- references/: Genome reference files
- cellranger/: cellranger-atac outputs
- filtered/: Quality-filtered data
- analysis/: Downstream analysis results
- plots/: Generated visualizations
- reports/: Analysis reports
- logs/: Processing logs

## Quick Start
1. Add FASTQ files to raw_data/ directory
2. Update samples.csv with sample information
3. Run: /bio scatac upstream ./raw_data
EOF
        """
        return init_response

    def run_upstream_workflow_check_dependencies(self):
        """Run dependency check workflow"""
        logger.info("Running dependency check workflow")
        check_dependencies_response = f"""
# Check scATAC-seq Tool Dependencies

# Check primary tools
which cellranger-atac || echo "Missing: cellranger-atac - Download from 10X Genomics"
which python3 || echo "Missing: python3 - conda install python"
which R || echo "Missing: R - conda install r-base"
which bedtools || echo "Missing: bedtools - conda install -c bioconda bedtools"
which samtools || echo "Missing: samtools - conda install -c bioconda samtools"

# Check versions
echo "Tool versions:"
cellranger-atac --version 2>/dev/null | head -1
python3 --version 2>/dev/null
R --version 2>/dev/null | head -1
bedtools --version 2>/dev/null
samtools --version 2>/dev/null | head -1

# Check Python packages
python3 -c "import scanpy; print('✅ scanpy installed')" 2>/dev/null || echo "Missing: scanpy - pip install scanpy"
python3 -c "import anndata; print('✅ anndata installed')" 2>/dev/null || echo "Missing: anndata - pip install anndata"
python3 -c "import pandas; print('✅ pandas installed')" 2>/dev/null || echo "Missing: pandas - pip install pandas"

# Check R packages
R -e "library(Signac); cat('✅ Signac installed\\n')" 2>/dev/null || echo "Missing: Signac - install.packages('Signac')"
R -e "library(Seurat); cat('✅ Seurat installed\\n')" 2>/dev/null || echo "Missing: Seurat - install.packages('Seurat')"
        """
        return check_dependencies_response

    def run_upstream_workflow_install_cellranger(self):
        """Run cellranger-atac installation workflow"""
        logger.info("Running cellranger-atac installation workflow")
        install_cellranger_response = f"""
# Install cellranger-atac v2.2.0

# Create software directory
mkdir -p software
cd software

# Download cellranger-atac (use 10X Genomics download link)
echo "Downloading cellranger-atac v2.2.0..."
wget -O cellranger-atac-2.2.0.tar.gz "https://cf.10xgenomics.com/releases/cell-atac/cellranger-atac-2.2.0.tar.gz"

# Extract
echo "Extracting cellranger-atac..."
tar -xzf cellranger-atac-2.2.0.tar.gz

# Make executable
chmod +x cellranger-atac-2.2.0/bin/cellranger-atac

# Test installation
./cellranger-atac-2.2.0/bin/cellranger-atac --version

# Add to PATH (optional)
echo "To use cellranger-atac globally, add to your PATH:"
echo "export PATH=\\"$(pwd)/cellranger-atac-2.2.0/bin:\$PATH\\""

# Clean up download
rm cellranger-atac-2.2.0.tar.gz
        """
        return install_cellranger_response

    def run_upstream_workflow_setup_reference(self):
        """Run reference genome setup workflow"""
        logger.info("Running reference genome setup workflow")
        setup_reference_response = f"""
# Setup scATAC-seq Reference Genome

# Create references directory
mkdir -p references/human references/mouse

# Download human GRCh38 reference (latest)
echo "Downloading human GRCh38 reference..."
cd references/human
wget https://cf.10xgenomics.com/supp/cell-arc/refdata-cellranger-arc-GRCh38-2024-A.tar.gz
tar -xzf refdata-cellranger-arc-GRCh38-2024-A.tar.gz
rm refdata-cellranger-arc-GRCh38-2024-A.tar.gz
cd ../..

# Download mouse GRCm39 reference (optional)
echo "Downloading mouse GRCm39 reference..."
cd references/mouse  
wget https://cf.10xgenomics.com/supp/cell-arc/refdata-cellranger-arc-GRCm39-2024-A.tar.gz
tar -xzf refdata-cellranger-arc-GRCm39-2024-A.tar.gz
rm refdata-cellranger-arc-GRCm39-2024-A.tar.gz
cd ../..

echo "Reference genomes downloaded successfully!"
echo "Human reference: references/human/refdata-cellranger-arc-GRCh38-2024-A"
echo "Mouse reference: references/mouse/refdata-cellranger-arc-GRCm39-2024-A"
        """
        return setup_reference_response

    def run_upstream_workflow_scan_folder(self):
        """Run folder scanning workflow"""
        logger.info("Running folder scanning workflow")
        scan_folder_response = f"""
# Scan scATAC-seq Data Folder

# List all FASTQ files
echo "=== FASTQ Files ==="
find . -name "*.fastq.gz" -o -name "*.fq.gz" | head -20

# Check for 10X Chromium format
echo "=== 10X Chromium Format Check ==="
find . -name "*_S*_L00*_R*_001.fastq.gz" | head -10
if [ $? -eq 0 ]; then
    echo "✅ 10X Chromium format detected"
else
    echo "⚠️ Non-standard format detected"
fi

# Count samples by extracting sample names
echo "=== Sample Detection ==="
find . -name "*_S*_L00*_R*_001.fastq.gz" | sed 's/_S[0-9]*_L00.*//' | sort | uniq -c

# Check file sizes
echo "=== File Sizes ==="
find . -name "*.fastq.gz" -exec ls -lh {{}} + | awk '{{print $5, $9}}' | head -10

# Check for existing cellranger outputs
echo "=== Existing Analysis ==="
find . -name "web_summary.html" -o -name "fragments.tsv.gz" -o -name "*.h5" | head -10

echo "Folder scan complete!"
        """
        return scan_folder_response

    def run_upstream_workflow_run_count(self):
        """Run cellranger-atac count workflow"""
        logger.info("Running cellranger-atac count workflow")
        run_count_response = f"""
# Run cellranger-atac count

# Set variables (adjust as needed)
SAMPLE_ID="sample_name"
FASTQS_PATH="/path/to/fastqs"
REFERENCE_PATH="/path/to/reference"
OUTPUT_DIR="cellranger_output"
EXPECTED_CELLS=10000

# Create output directory
mkdir -p $OUTPUT_DIR
cd $OUTPUT_DIR

# Run cellranger-atac count
cellranger-atac count \\
    --id=$SAMPLE_ID \\
    --fastqs=$FASTQS_PATH \\
    --reference=$REFERENCE_PATH \\
    --expect-cells=$EXPECTED_CELLS \\
    --localcores=4 \\
    --localmem=64

# Check outputs
echo "Checking cellranger outputs..."
ls -la $SAMPLE_ID/outs/

# Validate key files
for file in web_summary.html summary.csv fragments.tsv.gz filtered_peak_bc_matrix.h5 peaks.bed possorted_bam.bam; do
    if [ -f "$SAMPLE_ID/outs/$file" ]; then
        echo "✅ $file found"
    else
        echo "❌ $file missing"
    fi
done

echo "cellranger-atac count completed!"
        """
        return run_count_response

    def run_upstream_workflow_test_functionality(self):
        """Run functionality test workflow"""
        logger.info("Running functionality test workflow")
        test_functionality_response = f"""
# Test cellranger-atac Functionality

# Test 1: Check binary exists and is executable
CELLRANGER_PATH="software/cellranger-atac-2.2.0/bin/cellranger-atac"
if [ -x "$CELLRANGER_PATH" ]; then
    echo "✅ Binary is executable"
else
    echo "❌ Binary not found or not executable"
    exit 1
fi

# Test 2: Version check
echo "Testing version command..."
$CELLRANGER_PATH --version
if [ $? -eq 0 ]; then
    echo "✅ Version command works"
else
    echo "❌ Version command failed"
fi

# Test 3: Help command
echo "Testing help command..."
$CELLRANGER_PATH --help | head -10
if [ $? -eq 0 ]; then
    echo "✅ Help command works"
else
    echo "❌ Help command failed"
fi

# Test 4: Count subcommand
echo "Testing count subcommand..."
$CELLRANGER_PATH count --help | head -5
if [ $? -eq 0 ]; then
    echo "✅ Count subcommand accessible"
else
    echo "❌ Count subcommand failed"
fi

# Test 5: Check dependencies (optional)
echo "Checking library dependencies..."
ldd $CELLRANGER_PATH 2>/dev/null | grep "not found" || echo "✅ No missing library dependencies"

echo "Functionality test completed!"
        """
        return test_functionality_response