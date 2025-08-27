# Bio Toolset Development Guide

## Overview
This guide provides design patterns and architecture for developing bioinformatics analysis toolsets in Pantheon. The bio toolsets use a **workflow-based template system** that generates bash command templates rather than executing hardcoded workflows.

## Current Architecture

### 🧬 Template-Driven Workflow System
All bio toolsets follow a **template-driven approach**:
1. **Workflow functions** return **bash command templates**
2. **Users adapt templates** to their specific data and paths
3. **Users execute adapted commands** using the bash tool
4. **Analysis and verification** of results by users

### Available Toolsets

#### 🧬 ATAC-seq Analysis (`atac/`)
- **upstream.py**: Template workflows for QC, alignment, BAM processing
- **analysis.py**: Template workflows for peak calling, motif analysis, visualization
- **Key pattern**: `atac.ATAC_Upstream(workflow_type)` → returns bash templates

#### 🧬 RNA-seq Analysis (`rna/`)
- **upstream.py**: Template workflows for QC, alignment, quantification  
- **analysis.py**: Template workflows for differential expression, pathway analysis
- **Key pattern**: `rna.RNA_Upstream(workflow_type)` → returns bash templates

#### 🧬 Single-cell ATAC-seq (`scatac/`)
- **upstream.py**: Template workflows for cell processing, alignment
- **analysis.py**: Template workflows for clustering, peak analysis
- **Key pattern**: `scatac.ScATAC_Upstream(workflow_type)` → returns bash templates

---

# DatabaseQuery Toolset (OmicVerse DataCollect)

The DatabaseQuery toolset exposes a natural language command to query public biological databases through OmicVerse's `external.datacollect`, automatically selecting an appropriate client and output format.

## Quick Start

- Natural language query (auto-route):

  - `/bio database_query "PDB structure 1CRN"`
  - `/bio database_query "UniProt protein info for TP53 as DataFrame"`
  - `/bio database_query "GEO GSE72056 to AnnData"`
  - `/bio database_query "KEGG hsa04110 pathway as pandas"`
  - `/bio database_query "variant rs429358 details"`
  - `/bio database_query "Reactome R-HSA-109581"`

- List supported sources:

  - `/bio database_query list_sources`

## How It Works

- The toolset attempts routing in this order:
  1. LLM-based routing via a configured magique Agent service (optional)
  2. Heuristic fallback using identifier patterns and keywords

- When wrappers exist, it uses the high-level `DataCollect` entrypoints:
  - Protein: `collect_protein_data` (sources: `uniprot|pdb|alphafold|interpro|string`)
  - Expression: `collect_expression_data` (sources: `geo|ccre`)
  - Pathway: `collect_pathway_data` (sources: `kegg|reactome|gtopdb`)

- Otherwise it instantiates the recommended client (e.g., `EnsemblClient`, `dbSNPClient`) and converts results to the requested format when possible (`pandas`, `AnnData`, `MuData`).

## Optional LLM Routing

- Set an Agent service id to enable LLM routing:

  - Env var: `PANTHEON_AGENT_SERVICE_ID=<agent_service_id>`
  - Or pass per-call flag: `--llm_service_id <agent_service_id>`

- If the Agent is unavailable, routing falls back to heuristics automatically.

## Recognized Patterns (Heuristics)

- Protein/Structure:
  - PDB IDs: 4-char codes (e.g., `1CRN`) with keywords like `pdb`, `structure`
  - UniProt: IDs or symbols with `uniprot`, `protein`
  - AlphaFold: keywords `alphafold`, `af-`
  - STRING: keywords `string`, `interaction`

- Expression:
  - GEO accessions: `GSE\d+`, keywords `geo`, `expression`, `rna` (default output: `AnnData`)

- Pathways:
  - KEGG: `hsa\d{5}`, keyword `kegg`
  - Reactome: `R-HSA-\d+`, keyword `reactome`

- Genomics:
  - dbSNP: `rs\d+`
  - Ensembl: `ENSG\d+`, keyword `ensembl`

## Return Structure

- The command returns a dictionary with:
  - `success`: boolean
  - `payload`: result object (DataFrame/AnnData/MuData/dict)
  - `decision`: `{category, client, source, identifier, to_format, strategy, rationale}`
  - `query`: original input string
  - `error`: present when `success` is false

## Troubleshooting

- Missing OmicVerse DataCollect:
  - Ensure your environment has a working `omicverse` install with `external.datacollect` present.

---

# SingleCellAgent Toolset (Embedded)

End-to-end single-cell RNA-seq analysis powered by OmicVerse and exposed via Pantheon without external imports. Supports QC, annotation, trajectory, DE, enrichment, and visualization.

## Quick Start

- Run comprehensive analysis:
  - `/bio SingleCellAgent pbmc3k.h5ad`

- Select analysis type:
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type annotation`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type trajectory`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type differential`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type visualization`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type qc`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type clustering`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type batch_integration`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type communication`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type grn`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type drug`
  - `/bio SingleCellAgent pbmc3k.h5ad --analysis_type metacell`

- Output format and saving:
  - `--output_format detailed|summary|structured`
  - `--save_results true` to write JSON + figures under `singlecell_results/`

## Notes

- Uses embedded helpers under `bio/single_cell_agent/single_cell_agent_deps` (no external imports required).
- Requires environment with `omicverse`, `scanpy`, and plotting deps for full functionality. Modules like CPDB/SCENIC may need additional setup; friendly guidance is returned when unavailable.


- LLM routing not used:
  - Set `PANTHEON_AGENT_SERVICE_ID` or pass `--llm_service_id`. Otherwise, heuristics are used.

- Conversion errors:
  - AnnData/MuData conversion requires `anndata`/`mudata`. If missing, results fall back to dicts or DataFrames.

## File Locations

- Toolset: `pantheon/toolsets/bio/database_query.py`
- Manager wiring: `pantheon/toolsets/bio/__init__.py`

## Core Design Principles

### 1. Template-Based Workflow Architecture

```python
@tool
def TOOLSET_Upstream(self, workflow_type: str, description: str = None):
    """Template-based workflow dispatcher"""
    if workflow_type == "workflow_name":
        return self.run_upstream_workflow_workflow_name()
    # ... other workflows
    else:
        return "Invalid workflow type"

def run_upstream_workflow_workflow_name(self):
    """Return bash command template"""
    logger.info("Running workflow_name workflow")
    template_response = f"""
# Workflow Name - Command Templates

# Step 1: Description
command_template_1 --param1 value1 --param2 value2

# Step 2: Description  
command_template_2 input.file output.file

# Notes and guidance for adaptation
"""
    return template_response
```

### 2. Configuration-Driven Tool Lists

Each toolset maintains comprehensive tool categorization:

```python
def _initialize_config(self) -> Dict[str, Any]:
    return {
        "file_extensions": {
            # Expected file types for pattern matching
            "input": [".fastq", ".fq", ".fastq.gz"],
            "output": [".bam", ".sam", ".bigwig"],
            "reference": [".fa", ".fasta", ".gtf"]
        },
        "tools": {
            # Tool categories for dependency checking
            "qc": ["fastqc", "multiqc"],
            "alignment": ["tool1", "tool2"], 
            "quantification": ["tool3", "tool4"]
        },
        "default_params": {
            # Sensible parameter defaults
            "threads": 8,
            "memory": "16G"
        }
    }
```

### 3. Workflow Template Patterns

#### Pattern A: Project Setup
```python
def run_upstream_workflow_init(self):
    """Project initialization template"""
    return f"""
# Initialize Project Structure
mkdir -p project/{{raw_data,processed,results,logs}}

# Create config file
cat > project/config.json << EOF
{{
  "project_name": "analysis",
  "created": "$(date)",
  "pipeline_version": "1.0.0"
}}
EOF
"""
```

#### Pattern B: Tool Dependency Check
```python
def run_upstream_workflow_check_dependencies(self):
    """Dependency check template"""
    return f"""
# Check Tool Dependencies
which tool1 || echo "Missing: tool1 - install command"
which tool2 || echo "Missing: tool2 - install command"

# Check versions
tool1 --version
tool2 --version
"""
```

#### Pattern C: Data Processing
```python
def run_upstream_workflow_process_data(self):
    """Data processing template"""
    return f"""
# Process Data Templates

# Single-end processing
tool_command input.fastq.gz -o output_dir/

# Paired-end processing  
tool_command input_R1.fastq.gz input_R2.fastq.gz -o output_dir/

# Multiple samples
for sample in *.fastq.gz; do
    tool_command $sample -o results/
done
"""
```

## Implementation Guidelines

### 1. Workflow Function Naming Convention

```python
# Standard pattern
def run_upstream_workflow_{workflow_name}(self):
def run_analysis_workflow_{workflow_name}(self):

# Examples
def run_upstream_workflow_align_star(self):
def run_upstream_workflow_quantify_featurecounts(self):
def run_analysis_workflow_differential_expression(self):
```

### 2. Template Response Format

```python
def run_upstream_workflow_example(self):
    """Workflow description"""
    logger.info("Running example workflow")
    template_response = f"""
# Workflow Title - Clear Description

# Step 1: What this step does
command_template --required-param value --optional-param value

# Step 2: What this step does
command_template input.file output.file

# Step 3: What this step does with explanation
command_template --complex-params | processing_command

# Notes:
# - Adaptation guidance
# - Common parameter adjustments
# - Expected outputs
"""
    return template_response
```

### 3. Constructor Pattern (REQUIRED)

```python
def __init__(
    self,
    name: str = "toolset_name",
    workspace_path: str | Path | None = None,
    worker_params: dict | None = None,
    **kwargs,
):
    super().__init__(name, worker_params, **kwargs)
    self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
    self.pipeline_config = self._initialize_config()  # REQUIRED
    self.console = Console()  # REQUIRED for rich output
```

## Current Workflow Implementations

### RNA-seq Upstream Workflows

The RNA toolset implements 10 template workflows via `rna.RNA_Upstream(workflow_type)`:

1. **"init"** - Project structure initialization
2. **"check_dependencies"** - Tool availability checking
3. **"setup_genome_resources"** - Genome/transcriptome download and indexing
4. **"run_fastqc"** - Quality control with FastQC
5. **"trim_adapters"** - Adapter trimming with Trim Galore
6. **"align_star"** - STAR alignment (two-pass mode)
7. **"align_hisat2"** - HISAT2 alignment (alternative)
8. **"quantify_featurecounts"** - featureCounts quantification (alignment-based, default)
9. **"process_bam_smart"** - Comprehensive BAM processing
10. **"rna_qc"** - RNA-seq specific quality control

### RNA-seq Analysis Workflows

The RNA toolset implements 3 downstream analysis workflows via `rna.RNA_Analysis(workflow_type)`:

1. **"differential_expression"** - DESeq2 differential expression analysis
2. **"pathway_analysis"** - Gene set enrichment with clusterProfiler
3. **"visualization"** - Generate PCA plots, volcano plots, heatmaps

### ATAC-seq Upstream Workflows

The ATAC toolset implements 10 template workflows via `atac.ATAC_Upstream(workflow_type)`:

1. **"init"** - Project structure initialization
2. **"check_dependencies"** - Tool availability checking
3. **"setup_genome_resources"** - Genome download and indexing
4. **"run_fastqc"** - Quality control with FastQC
5. **"trim_adapters"** - Adapter trimming
6. **"align_bowtie2"** - Bowtie2 alignment (recommended for ATAC)
7. **"align_bwa"** - BWA alignment (alternative)
8. **"filter_bam"** - BAM filtering for ATAC-seq
9. **"mark_duplicates"** - PCR duplicate removal
10. **"process_bam_smart"** - Comprehensive BAM processing

## Usage Examples

### Basic Template Workflow

```python
# 1. Get bash command template
template_commands = rna.RNA_Upstream("align_star")

# 2. Template contains:
"""
# RNA-seq Alignment with STAR

# Two-pass mapping for improved splice junction detection
STAR --genomeDir genome/index/star/ \
    --readFilesIn sample_R1_val_1.fq.gz sample_R2_val_2.fq.gz \
    --readFilesCommand zcat \
    --runThreadN 8 \
    --outFileNamePrefix sample_ \
    --outSAMtype BAM SortedByCoordinate
"""

# 3. User adapts to their data:
# - Replace genome/index/star/ with actual index path
# - Replace sample_R1_val_1.fq.gz with actual fastq files
# - Adjust thread count for their system

# 4. User executes adapted commands using bash tool
```

### Complete RNA-seq Analysis Pipeline

```python
# Phase 1: Setup
init_template = rna.RNA_Upstream("init")
# Execute: mkdir commands, create config

deps_template = rna.RNA_Upstream("check_dependencies") 
# Execute: which commands, version checks

genome_template = rna.RNA_Upstream("setup_genome_resources")
# Execute: wget commands, index building

# Phase 2: Data Processing  
qc_template = rna.RNA_Upstream("run_fastqc")
# Execute: fastqc commands on your FASTQ files

trim_template = rna.RNA_Upstream("trim_adapters")
# Execute: trim_galore commands

align_template = rna.RNA_Upstream("align_star")
# Execute: STAR commands with your paths

quant_template = rna.RNA_Upstream("quantify_featurecounts")
# Execute: featureCounts commands for gene expression quantification

# Phase 3: Analysis
de_template = rna.RNA_Analysis("differential_expression")
# Execute: R scripts for DESeq2 analysis
```

## Key Differences from Hardcoded Systems

| Aspect | Template System (Current) | Hardcoded System (Traditional) |
|--------|---------------------------|--------------------------------|
| **Execution** | Returns bash templates | Executes commands directly |
| **Flexibility** | User adapts to their data | Fixed to specific assumptions |
| **Error Handling** | User handles execution errors | System handles execution |
| **Customization** | Full command customization | Limited parameter options |
| **Learning** | Users see exact commands | Commands hidden from users |
| **Debugging** | Users can inspect/modify commands | Black box execution |

## Benefits of Template Approach

1. **🔧 Flexibility**: Users can adapt commands to any data structure
2. **📚 Educational**: Users learn the actual bioinformatics commands
3. **🐛 Debugging**: Users can inspect and modify commands before execution
4. **⚡ Performance**: No overhead from hardcoded assumptions
5. **🔄 Reproducibility**: Users have exact command history
6. **🎯 Customization**: Full control over parameters and paths

## Development Guidelines

### 1. Creating New Workflows

```python
def run_upstream_workflow_new_workflow(self):
    """New workflow description"""
    logger.info("Running new workflow")
    return f"""
# New Workflow - Description

# Clear step-by-step command templates
command1 --param1 value1
command2 input.file output.file

# Include adaptation guidance
# - Parameter explanations
# - Common modifications needed
# - Expected output files
"""
```

### 2. Tool Configuration Updates

```python
def _initialize_config(self) -> Dict[str, Any]:
    return {
        "tools": {
            "new_category": ["tool1", "tool2"],  # Add new tool categories
            "existing_category": ["existing_tools", "new_tool"]  # Extend existing
        }
    }
```

### 3. Testing Templates

```python
# Test that workflow templates are returned correctly
def test_workflow_template():
    toolset = RNASeqUpstreamToolSet()
    template = toolset.RNA_Upstream("workflow_name")
    
    assert isinstance(template, str)
    assert "command_template" in template
    assert template.startswith("#")  # Should be formatted comments/commands
```

### Key Observations from ATAC Implementation

1. **Rich Console Integration**: Every method uses `self.console` for formatted output
2. **Progress Tracking**: Long operations always use `Progress()` context manager
3. **Structured Returns**: All methods return dicts with `status` field
4. **Error Handling**: Try/except blocks with meaningful error messages
5. **Configuration-Driven**: Uses `self.pipeline_config` throughout
6. **File Management**: Consistent use of `Path` objects
7. **Table/Panel Display**: Results shown in formatted tables and panels
8. **Multi-Source Downloads**: Tests multiple mirrors for fastest download
9. **Auto-Detection**: Detects paired-end, species, analysis stage automatically
10. **Installation Helpers**: Detects package manager and installs missing tools
## Integration Checklist

- [ ] Create directory structure under `bio/[tool_name]/`
- [ ] Implement template-based workflow functions
- [ ] Return formatted bash command templates (not execute directly)
- [ ] Include clear adaptation guidance in templates
- [ ] Configure tool categories in `_initialize_config()`
- [ ] Follow naming convention: `run_upstream_workflow_*`
- [ ] Add CLI prompt integration
- [ ] Add UI workflow recognition
- [ ] Test template generation (not execution)
- [ ] Document workflow types and their purposes

This template-based architecture provides maximum flexibility while maintaining guidance and best practices for bioinformatics analysis workflows.
