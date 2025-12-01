# Single-cell ATAC-seq Upstream Analysis

## Overview

Single-cell ATAC-seq analysis pipeline using cellranger-atac and downstream analysis tools. This skill provides workflow-based commands for processing 10X Genomics single-cell chromatin accessibility data from raw FASTQ files through quality control, dimensionality reduction, clustering, and peak annotation.

## Main Workflow

### Upstream Analysis

**Parameters:** `folder_path` - Target folder containing 10X Chromium FASTQ files

**Global Rules:**
- Always use the provided `folder_path` in all phases
- Idempotent behavior: NEVER create duplicate todos. Only create if the list is EMPTY
- Do not ask the user for confirmations; proceed automatically and log warnings when needed
- After each concrete tool completes successfully, call `mark_task_done("what was completed")`, then `show_todos()`
- All python code should use `run_python()` to execute. Don't use `shell.run_command()` or `python - <<` for python code

### Phase 0 - Smart cellranger-atac Detection & Setup (AI-Driven)

#### Priority 1 - Test System-wide Command

```bash
cellranger-atac --version
```
- If SUCCESS: Already available in PATH - skip all installation
- If FAIL: Continue to Priority 2

#### Priority 2 - Search Software Directories Only

```bash
find ./software -name 'cellranger-atac*' 2>/dev/null
find ~/software -name 'cellranger-atac*' 2>/dev/null
```

For each found path (prioritize ./software):
- Test: `path_to_cellranger-atac --version`
- If working: Found working installation - set PATH and use it
- Export PATH: `export PATH=$(dirname path_to_cellranger-atac):$PATH`
- Re-test: `cellranger-atac --version`
- If any working installation found: skip installation

#### Priority 3 - Local Installation Path Selection

- Only if no working installation found anywhere
- Analyze environment and choose optimal installation path
- Check candidate paths: `./software`, `~/software`, `/tmp/software`
- Consider: write permissions, disk space, persistence needs
- Execute: `install_cellranger_atac(install_dir=chosen_path)`

### Reference Genome Setup (AI-driven Priority Detection)

#### Priority 1 - Check for Existing References

```python
scatac.check_reference_status(species="human")
scatac.check_reference_status(species="mouse")
```
- If `recommendation="ready"`: Reference already available - skip download

#### Priority 2 - Search Reference Directories

```bash
find ./references -name 'refdata-*' 2>/dev/null
find ~/references -name 'refdata-*' 2>/dev/null
```
- Test found references for completeness (genome.fa, genes.gtf files)
- If complete reference found: skip download

#### Priority 3 - Check for Existing Downloads

- Look for downloaded reference archives (*.tar.gz)
- If found: extract and verify

#### Priority 4 - Download if Needed

- Analyze environment and choose optimal reference path
- Consider: `./references` vs `~/references` vs `workspace/references`
- Execute: `setup_reference(species=detected_species)`
- You can ask the user for the species and genome version if auto-detection fails

### Phase 1 - TODO Creation (Strict De-dup)

Mandatory order:
1. `current = show_todos()`
2. `scan_folder("{folder_path}")`

Creation rule:
- If current todos contain ONLY setup/installation tasks → create the analysis todos:
  - "Validate and rename 10X Chromium FASTQ files for cellranger-atac"
  - "Setup reference genome for cellranger-atac"
  - "Run cellranger-atac count for each sample"
  - "Load cellranger outputs for downstream analysis"
  - "Perform quality control filtering"
  - "Compute dimensionality reduction (LSI/PCA/UMAP)"
  - "Find cell clusters using graph-based clustering"
  - "Annotate peaks with genomic features"
  - "Generate comprehensive scATAC-seq analysis report"
- If current is completely EMPTY → create ALL todos including:
  - "Check and install cellranger-atac if needed"
  - + all above analysis todos
- If analysis todos already exist → DO NOT create duplicates. Work with existing todos.

### Phase 2 - Execute with TODO Tracking (Loop)

For each current task:
1. `hint = execute_current_task()` - obtain guidance for the next action
2. Run the appropriate scATAC tool:
   - For FASTQ validation/renaming: 10X_FASTQ_CHECKER_AND_RENAMER (detailed below)
   - For installation: `check_installation_status()`, `install_cellranger_atac()`
   - For reference: `setup_reference()`
   - For cellranger: `run_count(sample_id, fastqs_path, reference_path)`
   - For downstream: `load_cellranger_data()`, `run_quality_control()`, `compute_embeddings()`, `find_clusters()`
   - For annotation: `annotate_peaks()`
   - For reporting: `generate_report()`
3. `mark_task_done("brief, precise description of the completed step")`
4. `show_todos()`

Repeat until all todos are completed.

### Phase 3 - Adaptive TODO Refinement

- If installation fails → `add_todo("Troubleshoot cellranger-atac installation")`
- If quality issues found → `add_todo("Address data quality issues in scATAC")`
- If additional analysis needed → `add_todo("Additional single-cell analysis")`

---

## 10X FASTQ Checker and Renamer Protocol

**MANDATORY EXECUTION** when task contains "Validate and rename 10X":

### Step 1 - List and Analyze Current Files

```bash
ls -la {folder_path}/*fastq.gz
ls -la {folder_path}/*.fq.gz
```

### Step 2 - Check 10X Format Compliance (CRITICAL)

- **REQUIRED:** `samplename_S1_L00X_R1_001.fastq.gz`, `samplename_S1_L00X_R2_001.fastq.gz`, `samplename_S1_L00X_R3_001.fastq.gz`
- **CURRENT:** any other naming pattern (like `atac_pbmc_1k_nextgem_*`)
- **DECISION:** If current naming != required naming → PROCEED TO STEP 4 RENAMING

### Step 3 - Inspect FASTQ Headers to Confirm 10X Format

```bash
zcat {folder_path}/*R1*fastq.gz | head -4
zcat {folder_path}/*R2*fastq.gz | head -4
zcat {folder_path}/*R3*fastq.gz | head -4
```
- Verify 10X barcode/UMI structure in headers

### Step 4 - MANDATORY Rename (if files don't match S1_L00X_RX_001 pattern)

- Extract sample name from current files:
  - Example: `atac_pbmc_1k_nextgem_S1_L001_R1_001.fastq.gz` → extract `pbmc_1k`
  - Rule: Take meaningful part, remove technical prefixes like `atac_` and suffixes like `_nextgem`
- Identify current file patterns and map to new names:

```bash
# Find current R1 file
ls {folder_path}/*R1*fastq.gz
# Find current R2 file
ls {folder_path}/*R2*fastq.gz
# Find current R3 file
ls {folder_path}/*R3*fastq.gz
```

- Generate new names using extracted sample name (e.g., `extracted_sample_S1_L001_R1_001.fastq.gz`)
- Execute renaming commands with actual filenames:

```bash
mv [actual_R1_filename] [extracted_sample]_S1_L001_R1_001.fastq.gz
mv [actual_R2_filename] [extracted_sample]_S1_L001_R2_001.fastq.gz
mv [actual_R3_filename] [extracted_sample]_S1_L001_R3_001.fastq.gz
echo 'Renamed files: [old_names] → [new_names]' > rename_log.txt
```

### Step 5 - VERIFY Final Structure

```bash
ls -la {folder_path}/*S1_L001_R*_001.fastq.gz
```
- CONFIRM: All 3 files (R1, R2, R3) exist with correct naming
- Mark task complete ONLY after successful verification

---

## Execution Strategy

**MUST FOLLOW THIS ORDER:**

1. **SMART DETECTION:** Execute Priority 1-4 cellranger-atac detection workflow
2. **REFERENCE DETECTION:** Execute Priority 1-4 reference genome detection workflow
3. `show_todos()` → check current todo status
4. `scan_folder("{folder_path}")` → detect 10X format and samples
5. **TODO CREATION:** Apply smart creation rules based on current todo state:
   - If only setup/installation todos exist → create analysis pipeline todos
   - If completely empty → create full todo set
   - If analysis todos exist → skip creation (work with existing)
6. **FASTQ VALIDATION:** When current todo contains "Validate and rename 10X" →
   - IMMEDIATELY execute 10X_FASTQ_CHECKER_AND_RENAMER protocol (ALL 5 STEPS)
   - DO NOT skip Step 4 renaming - it is MANDATORY for cellranger-atac
7. Loop Phase 2 until all todos completed; refine with Phase 3 when needed

---

## Available Tools

- `check_installation_status()` - Check if cellranger-atac is installed
- `install_cellranger_atac(install_dir)` - Install cellranger-atac
- `check_reference_status(species)` - Check reference genome status
- `setup_reference(species)` - Download and setup reference genome
- `scan_folder(folder_path)` - Scan folder for FASTQ files
- `run_count(sample_id, fastqs_path, reference_path)` - Run cellranger-atac count
- `load_cellranger_data()` - Load cellranger outputs
- `run_quality_control()` - Perform QC filtering
- `compute_embeddings()` - Compute LSI/PCA/UMAP
- `find_clusters()` - Graph-based clustering
- `annotate_peaks()` - Annotate peaks with genomic features
- `generate_report()` - Generate analysis report
