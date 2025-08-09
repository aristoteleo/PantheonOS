"""Simplified ATAC-seq mode handler"""

from pathlib import Path
from typing import Optional

def generate_atac_analysis_message(folder_path: Optional[str] = None) -> str:
    """Generate ATAC-seq analysis message using ATAC toolset"""
    
    if folder_path:
        folder_path = Path(folder_path).resolve()
        
        message = f"""
🧬 ATAC-seq Analysis Pipeline — Strict Order & Idempotent Todo Management
Target folder: {folder_path}

You have access to the ATAC-seq toolset and TodoList management.

GLOBAL RULES
- Always use the provided folder_path: "{folder_path}" in all phases.
- Idempotent behavior: NEVER create duplicate todos. Only create if the list is EMPTY.
- Do not ask the user for confirmations; proceed automatically and log warnings when needed.
- After each concrete tool completes successfully, call mark_task_done("what was completed"), then show_todos().

PHASE 0 — SPECIES DETECTION & GENOME RESOURCES
1) Species detection:
   - species_info = atac.auto_detect_species("{folder_path}")
   - If confidence ≥ 2.0: accept.
   - If confidence < 2.0: accept the best guess automatically and LOG a warning note in the response.

2) Resource setup (comprehensive by default):
   - atac.setup_genome_resources(species_info.species, species_info.genome_version,
       include_gtf=True, include_blacklist=True)
   - Then validate:
     • atac.check_genome_integrity(species_info.species, species_info.genome_version)
     • atac.get_resource_info(species_info.species, species_info.genome_version)
   - Optional utilities:
     • atac.clean_incomplete_downloads()
     • atac.list_available_resources()

PHASE 1 — TODO CREATION (STRICT DE-DUP)
Mandatory order:
  a) current = show_todos()
  b) atac.scan_folder("{folder_path}")
Creation rule (single condition):
  • If current is EMPTY → create ONCE the following todos:
      0. "Setup reference genome automatically for Bowtie2 indexing"
      1. "ATAC-seq Quality Control with FastQC"
      2. "ATAC-seq Adapter Trimming with Trim Galore"
      3. "ATAC-seq Genome Alignment with Bowtie2"
      4. "ATAC-seq BAM Filtering (no duplicate removal)"
      5. "ATAC-seq Peak Calling with MACS2"
      6. "ATAC-seq Coverage Track Generation"
      7. "ATAC-seq QC Report Generation"
  • Else → DO NOT create anything. Work with the existing todos.

PHASE 2 — EXECUTE WITH TODO TRACKING (LOOP)
For each current task:
  1) hint = execute_current_task()   # obtain guidance for the next action
  2) Run the appropriate ATAC tool:
     - For alignment: atac.auto_align_fastq("{folder_path}")
     - For BAM processing: atac.process_bam_smart()   # default: no duplicate removal
     - For QC/report/others: follow 'hint' and use the corresponding atac.* tool
     - Tools auto-install dependencies when needed (bowtie2, samtools, etc.)
  3) mark_task_done("brief, precise description of the completed step")
  4) show_todos()
Repeat until all todos are completed.

PHASE 3 — ADAPTIVE TODO REFINEMENT
- If dependencies missing → add_todo("Install missing ATAC-seq tools")
- If quality issues found → add_todo("Address data quality issues")
- If additional analysis needed → add_todo("Additional analysis task")

EXECUTION STRATEGY (MUST FOLLOW THIS ORDER)
  1) atac.auto_detect_species("{folder_path}") → decide species/genome (auto, no user prompts; log warning if <2.0)
  2) atac.setup_genome_resources(...); validate with check/get_resource_info
  3) show_todos()
  4) atac.scan_folder("{folder_path}")
  5) If todos empty → create the standard set ONCE; else skip creation
  6) Loop Phase 2 until all done; refine with Phase 3 when needed

BEGIN NOW:
- Execute PHASE 0 step 1 → step 2 → then PHASE 1.
- Output should clearly show: species decision (with confidence & any warning), resource validation summary, todo status,
  and then progress through Phase 2 loop.
"""
        
    else:
        message = """
I need help with ATAC-seq analysis using your specialized toolsets.

You have access to comprehensive ATAC-seq and TODO management tools:

📋 TODO MANAGEMENT (use these for ALL tasks):
- add_todo() - Add tasks and auto-break them down
- show_todos() - Display current progress  
- execute_current_task() - Get smart guidance
- mark_task_done() - Mark tasks complete and progress

🧬 COMPLETE ATAC-seq TOOLSET:
SCANNING & SETUP:
- atac.scan_folder() - Comprehensive folder analysis
- atac.check_dependencies() - Check tool availability
- atac.install_missing_tools() - Auto-install missing tools
- atac.init() - Create project structure

QUALITY CONTROL:
- atac.validate_fastq() - Validate FASTQ files  
- atac.run_fastqc() - Quality control analysis

PREPROCESSING:
- atac.trim_adapters() - Remove adapters

ALIGNMENT & PROCESSING:
- atac.align_bwa() - BWA-MEM alignment
- atac.filter_bam() - Filter alignments
- atac.mark_duplicates() - Remove PCR duplicates

PEAK CALLING:
- atac.call_peaks_macs2() - MACS2 peak calling
- atac.call_peaks_genrich() - Genrich peak calling

VISUALIZATION & ANALYSIS:
- atac.bam_to_bigwig() - Generate coverage tracks
- atac.compute_matrix() - Matrix for heatmaps
- atac.plot_heatmap() - Create heatmaps
- atac.find_motifs() - Motif analysis
- atac.generate_atac_qc_report() - Comprehensive QC

GUIDANCE:
- atac.suggest_next_step() - Smart recommendations

Please start by adding a todo for your ATAC-seq analysis task, then use the appropriate ATAC tools!"""
    
    return message