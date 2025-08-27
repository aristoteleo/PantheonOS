"""RNA-seq Downstream Analysis - Differential expression, pathway analysis, and visualization"""

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

class RNASeqAnalysisToolSet(ToolSet):
    """RNA-seq Downstream Analysis Toolset - From quantified expression to biological insights"""
    
    def __init__(
        self,
        name: str = "rna_analysis",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.console = Console()
        
    @tool
    def RNA_Analysis(self, workflow_type: str, description: str = None):
        """Run a specific RNA-seq downstream analysis workflow"""
        if workflow_type == "differential_expression":
            return self.run_analysis_workflow_differential_expression()
        elif workflow_type == "pathway_analysis":
            return self.run_analysis_workflow_pathway_analysis()
        elif workflow_type == "visualization":
            return self.run_analysis_workflow_visualization()
        else:
            return "Invalid workflow type"
    
    def run_analysis_workflow_differential_expression(self):
        """Run differential expression analysis workflow"""
        logger.info("Running differential expression analysis workflow")
        de_response = f"""
# Differential Expression Analysis with DESeq2

# R script for DESeq2 analysis
Rscript -e "
library(DESeq2)

# Import featureCounts quantification data
count_data <- read.table('quantification/featurecounts/all_samples_counts.txt', header=TRUE, row.names=1, skip=1)

# Remove first 5 columns (gene info) and keep only count data
count_matrix <- count_data[,6:ncol(count_data)]

# Create sample metadata
sample_data <- read.table('samples.tsv', header=TRUE, row.names=1)
dds <- DESeqDataSetFromMatrix(countData=count_matrix, colData=sample_data, design = ~ condition)

# Run DESeq2
dds <- DESeq(dds)
res <- results(dds)

# Save results
write.csv(res, 'differential_expression_results.csv')
"
        """
        return de_response
    
    def run_analysis_workflow_pathway_analysis(self):
        """Run pathway analysis workflow"""
        logger.info("Running pathway analysis workflow")
        pathway_response = f"""
# Pathway Analysis with clusterProfiler

# R script for pathway analysis
Rscript -e "
library(clusterProfiler)
library(org.Hs.eg.db)

# Load DE results
de_results <- read.csv('differential_expression_results.csv', row.names=1)
significant_genes <- rownames(de_results[de_results\\$padj < 0.05,])

# GO enrichment
go_results <- enrichGO(gene=significant_genes, 
                      OrgDb=org.Hs.eg.db, 
                      keyType='SYMBOL',
                      ont='BP')

# KEGG pathway enrichment  
kegg_results <- enrichKEGG(gene=significant_genes,
                          organism='hsa')

# Save results
write.csv(go_results@result, 'go_enrichment.csv')
write.csv(kegg_results@result, 'kegg_enrichment.csv')
"
        """
        return pathway_response
    
    def run_analysis_workflow_visualization(self):
        """Run visualization workflow"""
        logger.info("Running visualization workflow")
        viz_response = f"""
# Generate RNA-seq Visualizations

# R script for generating plots
Rscript -e "
library(DESeq2)
library(ggplot2)
library(pheatmap)

# Load data
dds <- readRDS('dds.rds')
res <- read.csv('differential_expression_results.csv', row.names=1)

# PCA plot
pdf('pca_plot.pdf')
plotPCA(vst(dds), intgroup='condition')
dev.off()

# Volcano plot
pdf('volcano_plot.pdf')
ggplot(as.data.frame(res), aes(log2FoldChange, -log10(padj))) +
  geom_point() +
  theme_minimal()
dev.off()

# Heatmap of top genes
top_genes <- head(order(res\\$padj), 50)
pdf('heatmap.pdf')
pheatmap(assay(vst(dds))[top_genes,])
dev.off()
"
        """
        return viz_response