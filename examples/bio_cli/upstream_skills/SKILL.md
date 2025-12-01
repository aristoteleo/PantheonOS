# Upstream Skills Index

This directory contains bioinformatics upstream analysis skills for agent-assisted data processing. Each skill provides workflow guidance, code templates, and best practices for specific analysis types.

## Available Skills

| Skill | File | Description | Key Use Cases |
|-------|------|-------------|---------------|
| **ATAC-seq** | [atac.md](atac.md) | Bulk ATAC-seq analysis pipeline | Chromatin accessibility profiling, peak calling, motif analysis |
| **RNA-seq** | [rna.md](rna.md) | Bulk RNA-seq analysis pipeline | Transcriptome profiling, gene expression quantification |
| **scATAC-seq** | [scatac.md](scatac.md) | Single-cell ATAC-seq analysis | Single-cell chromatin accessibility, cellranger-atac processing |
| **scRNA-seq** | [scrna.md](scrna.md) | Single-cell RNA-seq analysis | Single-cell transcriptomics, cell type annotation with LLM |
| **Spatial** | [spatial.md](spatial.md) | Spatial transcriptomics analysis | Visium HD bin-to-cell conversion, spatial cell segmentation |

---

## Skill Summaries

### ATAC-seq (atac.md)

**Purpose:** Complete bulk ATAC-seq data processing from raw FASTQ files to peak calls and downstream analysis.

**Key Workflows:**
- Quality control with FastQC
- Adapter trimming with Trim Galore
- Genome alignment with Bowtie2/BWA
- BAM filtering and duplicate removal
- Peak calling with MACS2/Genrich
- Coverage track generation
- Motif analysis with HOMER

**Tools Used:** FastQC, Trim Galore, Bowtie2, BWA, samtools, Picard, MACS2, Genrich, deepTools, HOMER

---

### RNA-seq (rna.md)

**Purpose:** Complete bulk RNA-seq data processing from raw FASTQ files to gene expression quantification.

**Key Workflows:**
- Quality control with FastQC
- Adapter trimming
- Genome alignment with STAR/HISAT2
- Expression quantification with featureCounts
- BAM processing and QC
- Coverage track generation

**Tools Used:** FastQC, Trim Galore, STAR, HISAT2, featureCounts, samtools, MultiQC

---

### scATAC-seq (scatac.md)

**Purpose:** Single-cell ATAC-seq data processing using 10X Genomics cellranger-atac with downstream analysis.

**Key Workflows:**
- Smart cellranger-atac detection and installation
- 10X FASTQ file validation and renaming
- Reference genome setup
- cellranger-atac count execution
- Quality control filtering
- Dimensionality reduction (LSI/PCA/UMAP)
- Graph-based clustering
- Peak annotation

**Tools Used:** cellranger-atac, scanpy, custom downstream analysis tools

---

### scRNA-seq (scrna.md)

**Purpose:** Comprehensive single-cell RNA-seq analysis with omicverse integration and LLM-powered cell type annotation.

**Key Workflows:**
- Data loading and inspection
- Quality control with omicverse
- Preprocessing (normalization, HVG selection)
- PCA and batch correction
- Clustering and UMAP visualization
- Marker gene discovery (from data and description)
- AUCell scoring for cell type markers
- LLM-powered cell type annotation

**Tools Used:** scanpy, omicverse, AUCell, LLM annotation

**Special Features:**
- Persistent Python state management
- Context-aware marker generation
- Interactive LLM cell type annotation
- Structured results output

---

### Spatial Transcriptomics (spatial.md)

**Purpose:** Spatial transcriptomics analysis for Visium HD data with bin-to-cell conversion using omicverse.

**Key Workflows:**
- Visium 10X data loading
- H&E-based cell segmentation (Cellpose)
- Label expansion
- GEX-based cell segmentation
- Secondary label salvaging
- Bin-to-cell conversion

**Tools Used:** omicverse, Cellpose, scanpy

**Special Features:**
- Flexible execution (template-guided or direct implementation)
- Multi-modal cell segmentation (H&E + GEX)
- Intelligent label combination

---

## Common Patterns Across Skills

### TodoList Management
All skills use a consistent TodoList pattern:
- `show_todos()` - View current task list
- `add_todo("task")` - Add new task
- `mark_task_done("description")` - Complete current task
- `execute_current_task()` - Get guidance for next action

### Phase-based Execution
1. **Phase 0:** Setup and validation
2. **Phase 1:** TODO creation (strict de-duplication)
3. **Phase 2:** Execute with tracking (loop)
4. **Phase 3:** Adaptive refinement

### Python Environment Rules (for Python-based skills)
- Persistent state across calls
- Memory-first approach (avoid redundant I/O)
- Smart variable checking with try/except
- Error recovery without full restart

---

## How to Use

1. **Select the appropriate skill** based on your data type
2. **Read the skill document** to understand the workflow
3. **Follow the phase-based execution** pattern
4. **Adapt code templates** to your specific data
5. **Analyze results** at each step before proceeding
6. **Use TodoList** to track progress

---

## Integration Notes

These skills are designed to work with:
- Agent-based execution systems
- TodoList management tools
- Python persistent interpreters (for Python-based skills)
- Bash command execution (for pipeline skills)

Each skill can be invoked independently or as part of a larger analysis workflow.
