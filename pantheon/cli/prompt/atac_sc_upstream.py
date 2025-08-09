"""Single-cell ATAC-seq analysis mode handler"""

from pathlib import Path
from typing import Optional

def generate_scatac_analysis_message(folder_path: Optional[str] = None) -> str:
    """Generate scATAC-seq analysis message using scATAC toolset"""
    
    if folder_path:
        folder_path = Path(folder_path).resolve()
        
        message = f"""
🧬 Single-cell ATAC-seq Analysis Pipeline — cellranger-atac + Downstream Analysis
Target folder: {folder_path}

You have access to the scATAC-seq toolset and TodoList management.

GLOBAL RULES
- Always use the provided folder_path: "{folder_path}" in all phases.
- Idempotent behavior: NEVER create duplicate todos. Only create if the list is EMPTY.
- Do not ask the user for confirmations; proceed automatically and log warnings when needed.
- After each concrete tool completes successfully, call mark_task_done("what was completed"), then show_todos().

PHASE 0 — SMART CELLRANGER-ATAC DETECTION & SETUP (AI-DRIVEN)
1) Intelligent cellranger-atac availability check (priority order):
   
   PRIORITY 1 - Test system-wide command:
   - Run: shell.run_command("cellranger-atac --version") 
   - If SUCCESS: ✅ Already available in PATH - skip all installation
   - If FAIL: Continue to Priority 2
   
   PRIORITY 2 - Search existing installations:  
   - Use shell.run_command("find / -name 'cellranger-atac' 2>/dev/null | head -5")
   - For each found path (e.g., /opt/cellranger-atac-2.1.0/cellranger-atac):
     * Test: shell.run_command("path_to_cellranger-atac --version")
     * If working: ✅ Found working installation - set PATH and use it
     * Export PATH: shell.run_command("export PATH=/opt/cellranger-atac-2.1.0:$PATH")
     * Re-test: shell.run_command("cellranger-atac --version")
   - If any working installation found: skip installation
   
   PRIORITY 3 - Local installation path selection:
   - Only if no working installation found anywhere
   - Analyze environment and choose optimal installation path
   - Check candidate paths: "./software", "~/software", "/tmp/software"  
   - Consider: write permissions, disk space, persistence needs
   - Execute: install_cellranger_atac(install_dir=chosen_path)
   
   PRIORITY 4 - Final verification:
   - Always run: test_cellranger_functionality() to confirm working state

2) Reference genome setup:
   - setup_reference(species="human", auto_detect=True)  # Auto-detect from data
   - For mouse data: setup_reference(species="mouse")

PHASE 1 — TODO CREATION (STRICT DE-DUP)
Mandatory order:
  a) current = show_todos()
  b) scan_folder("{folder_path}")
Creation rule (single condition):
  • If current todos contain ONLY setup/installation tasks → create the analysis todos:
      1. "Validate and rename 10X Chromium FASTQ files for cellranger-atac"
      2. "Setup reference genome for cellranger-atac"  
      3. "Run cellranger-atac count for each sample"
      4. "Load cellranger outputs for downstream analysis"
      5. "Perform quality control filtering"
      6. "Compute dimensionality reduction (LSI/PCA/UMAP)"
      7. "Find cell clusters using graph-based clustering"
      8. "Annotate peaks with genomic features"
      9. "Generate comprehensive scATAC-seq analysis report"
  • If current is completely EMPTY → create ALL todos including:
      0. "Check and install cellranger-atac if needed"
      + all above analysis todos
  • If analysis todos already exist → DO NOT create duplicates. Work with existing todos.

PHASE 2 — EXECUTE WITH TODO TRACKING (LOOP)
For each current task:
  1) hint = execute_current_task()   # obtain guidance for the next action
  2) Run the appropriate scATAC tool:
     - For FASTQ validation/renaming: 10X_FASTQ_CHECKER_AND_RENAMER (detailed below)
     - For installation: check_installation_status(), install_cellranger_atac()
     - For reference: setup_reference()
     - For cellranger: run_count(sample_id, fastqs_path, reference_path)
     - For downstream: load_cellranger_data(), run_quality_control(), compute_embeddings(), find_clusters()
     - For annotation: annotate_peaks()
     - For reporting: generate_report()
  3) mark_task_done("brief, precise description of the completed step")
  4) show_todos()
Repeat until all todos are completed.

🔍 10X_FASTQ_CHECKER_AND_RENAMER PROTOCOL:
When validating/renaming 10X FASTQ files, follow this exact sequence:

STEP 1 - List and analyze current files:
- shell.run_command("ls -la {folder_path}/*fastq.gz")
- shell.run_command("ls -la {folder_path}/*.fq.gz") 

STEP 2 - Check 10X format compliance:
- Required naming: samplename_S1_L00X_R1_001.fastq.gz, samplename_S1_L00X_R3_001.fastq.gz, samplename_S1_L00X_R2_001.fastq.gz
- cellranger-atac needs R1 (reads), R2 (barcodes), R3 (reads) - NO R4
- Check if files follow this pattern vs current naming

STEP 3 - Inspect FASTQ headers to confirm 10X format:
- shell.run_command("zcat {folder_path}/*R1*fastq.gz | head -4")
- shell.run_command("zcat {folder_path}/*R2*fastq.gz | head -4") 
- shell.run_command("zcat {folder_path}/*R3*fastq.gz | head -4")
- Verify 10X barcode/UMI structure in headers

STEP 4 - Auto-rename if needed:
- If files don't match cellranger-atac naming requirements:
  * Generate new names following samplename_S1_L00X_RX_001.fastq.gz pattern
  * Use shell.run_command("mv old_name new_name") for each file
  * Create rename log: shell.run_command("echo 'old_name -> new_name' >> rename_log.txt")
  
STEP 5 - Verify final structure:
- shell.run_command("ls -la {folder_path}/*S1_L00*_R*_001.fastq.gz")
- Confirm all required R1, R2, R3 files exist for cellranger-atac count

PHASE 3 — ADAPTIVE TODO REFINEMENT
- If installation fails → add_todo("Troubleshoot cellranger-atac installation")
- If quality issues found → add_todo("Address data quality issues in scATAC")
- If additional analysis needed → add_todo("Additional single-cell analysis")

EXECUTION STRATEGY (MUST FOLLOW THIS ORDER)
  1) SMART DETECTION: Execute PRIORITY 1-4 cellranger-atac detection workflow
  2) show_todos() → check current todo status
  3) scan_folder("{folder_path}") → detect 10X format and samples
  4) TODO CREATION: Apply smart creation rules based on current todo state:
     - If only setup/installation todos exist → create analysis pipeline todos
     - If completely empty → create full todo set
     - If analysis todos exist → skip creation (work with existing)
  5) FASTQ VALIDATION: If "validate 10X FASTQ" todo exists → execute 10X_FASTQ_CHECKER_AND_RENAMER
  6) Loop Phase 2 until all todos completed; refine with Phase 3 when needed

BEGIN NOW:
- Start with SMART DETECTION: execute Priority 1-4 cellranger-atac detection workflow
- Then execute PHASE 0 → PHASE 1 → PHASE 2 loop  
- Output should clearly show: detection results at each priority level, installation status,
  reference setup summary, todo status, and then progress through Phase 2 loop.
"""
        
    else:
        message = """
I need help with single-cell ATAC-seq analysis using your specialized toolsets.

You have access to comprehensive scATAC-seq and TODO management tools:

📋 TODO MANAGEMENT (use these for ALL tasks):
- add_todo() - Add tasks and auto-break them down
- show_todos() - Display current progress  
- execute_current_task() - Get smart guidance
- mark_task_done() - Mark tasks complete and progress

🧬 COMPLETE scATAC-seq TOOLSET:
INSTALLATION & SETUP (SMART DETECTION):
⭐ PRIORITY 1: Test system command directly
   - shell.run_command("cellranger-atac --version")
⭐ PRIORITY 2: Search and configure existing installations  
   - shell.run_command("find / -name 'cellranger-atac' 2>/dev/null | head -5")
   - Test found paths and configure PATH if working
⭐ PRIORITY 3: Install only if no working version found
   - Analyze environment and choose installation path
   - scatac.install_cellranger_atac(install_dir=chosen_path) - Download and install
⭐ PRIORITY 4: Final verification
   - scatac.test_cellranger_functionality() - Confirm working state  
- scatac.setup_reference() - Download reference genomes (human/mouse)

SCANNING & PROJECT SETUP:
- scatac.scan_folder() - Comprehensive 10X data analysis
- scatac.check_dependencies() - Check tool availability
- scatac.init() - Create scATAC project structure

CELLRANGER-ATAC PROCESSING:
- scatac.run_count() - Run cellranger-atac count pipeline
- scatac.setup_references_batch() - Setup multiple references

DOWNSTREAM ANALYSIS:
- scatac.load_cellranger_data() - Load cellranger outputs
- scatac.run_quality_control() - Filter low-quality cells/peaks
- scatac.compute_embeddings() - LSI/PCA/UMAP dimensionality reduction
- scatac.find_clusters() - Graph-based clustering (Leiden/Louvain)
- scatac.annotate_peaks() - Peak-to-gene annotation

REPORTING:
- scatac.generate_report() - Comprehensive analysis report

GUIDANCE:
- scatac.suggest_next_step() - Smart recommendations

🚀 WORKFLOW: Start with SMART DETECTION (Priority 1-4) to find or install cellranger-atac,
then add todos for your scATAC-seq analysis task and use the appropriate scATAC tools!"""
    
    return message