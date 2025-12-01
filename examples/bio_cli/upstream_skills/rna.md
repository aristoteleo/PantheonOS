# RNA-seq Upstream Analysis

## Overview

Bulk RNA-seq analysis pipeline for transcriptome profiling. This skill provides workflow-based commands and TodoList management for complete RNA-seq data processing from raw FASTQ files to gene expression quantification.

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
- `rna.RNA_Upstream("init")` - Initialize project structure
- `rna.RNA_Upstream("check_dependencies")` - Check tool availability
- `rna.RNA_Upstream("setup_genome_resources")` - Setup genome resources

### Phase 1 - TODO Creation (Strict De-dup)

Mandatory order:
1. `current = show_todos()`
2. Analyze folder structure and FASTQ files

Creation rule (single condition):
- If current is EMPTY → create ONCE the following todos:
  - "Initialize RNA-seq project structure"
  - "Check and install RNA-seq dependencies"
  - "Setup genome resources and references"
  - "RNA-seq Quality Control with FastQC"
  - "RNA-seq Adapter Trimming"
  - "RNA-seq Genome Alignment with STAR"
  - "RNA-seq Expression Quantification"
  - "RNA-seq BAM Processing and QC"
  - "RNA-seq Coverage Track Generation"
  - "RNA-seq QC Report Generation"
- Else → DO NOT create anything. Work with the existing todos.

### Phase 2 - Execute with TODO Tracking (Loop)

**Critical Execution Strategy:**

When you call `rna.RNA_Upstream()` or `rna.RNA_Analysis()`, they return bash command templates. You MUST:

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

**Upstream Workflows** (`rna.RNA_Upstream(workflow_type)`):
- `"init"` - Initialize project structure
- `"check_dependencies"` - Check tool dependencies
- `"setup_genome_resources"` - Setup genome resources
- `"run_fastqc"` - Quality control analysis
- `"trim_adapters"` - Adapter trimming
- `"align_star"` - STAR alignment (recommended for RNA-seq)
- `"align_hisat2"` - HISAT2 alignment (alternative)
- `"quantify_featurecounts"` - featureCounts quantification (alignment-based)
- `"process_bam_smart"` - Smart BAM processing pipeline
- `"rna_qc"` - RNA-seq specific quality control

**Downstream Workflows** (`rna.RNA_Analysis(workflow_type)`):
- `"differential_expression"` - Differential expression analysis
- `"pathway_analysis"` - Gene set enrichment analysis
- `"visualization"` - Generate plots and visualizations

### Phase 3 - Adaptive TODO Refinement

- If dependencies missing → `add_todo("Install missing RNA-seq tools")`
- If quality issues found → `add_todo("Address data quality issues")`
- If additional analysis needed → `add_todo("Additional analysis task")`

---

## Execution Examples

### Step 1 - Project Initialization

```bash
# Get initialization commands
init_commands = rna.RNA_Upstream("init")
# Analyze and adapt the commands to your project
# Execute: mkdir -p project_structure, create config files, etc.
```

### Step 2 - Dependency Check

```bash
# Get dependency check commands
dep_commands = rna.RNA_Upstream("check_dependencies")
# Execute: which fastqc, which STAR, which featureCounts, etc.
# Install missing tools if needed
```

### Step 3 - FastQC Execution

```bash
# Get FastQC template commands
fastqc_commands = rna.RNA_Upstream("run_fastqc")
# Adapt to your actual FASTQ files
# Execute: fastqc sample_R1.fastq.gz sample_R2.fastq.gz -o qc/fastqc/
# Check results: ls qc/fastqc/*.html
# Analyze: Look for adapter contamination, quality issues
```

### Step 4 - Alignment Execution

```bash
# Get STAR alignment template commands
align_commands = rna.RNA_Upstream("align_star")
# Adapt with your actual file paths and genome index
# Execute: STAR --genomeDir genome/index/star/ --readFilesIn R1.fq.gz R2.fq.gz ...
# Check results: samtools flagstat sample_Aligned.sortedByCoord.out.bam
# Analyze: mapping rate, splice junction detection
```

### Step 5 - Quantification

```bash
# Get featureCounts quantification template commands
quant_commands = rna.RNA_Upstream("quantify_featurecounts")
# Adapt with your aligned BAM files
# Execute: featureCounts -a annotation.gtf -o counts.txt sample.bam
# Check results: head counts.txt
# Analyze: number of quantified genes, count statistics
```

---

## Critical Success Patterns

```bash
# Good - Check files before proceeding
ls *.fastq.gz  # Verify input files exist
fastqc *.fastq.gz -o qc/fastqc/
ls qc/fastqc/*.html  # Verify outputs created

# Good - Capture and analyze results
samtools flagstat sample_Aligned.sortedByCoord.out.bam > alignment_stats.txt
cat alignment_stats.txt  # Review mapping statistics

# Good - Error handling
if [ ! -f "sample_Aligned.sortedByCoord.out.bam" ]; then
    echo "ERROR: STAR alignment failed"
    exit 1
fi
```
