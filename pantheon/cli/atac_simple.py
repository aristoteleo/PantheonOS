"""Simplified ATAC-seq mode handler"""

from pathlib import Path
from typing import Optional


def generate_atac_analysis_message(folder_path: Optional[str] = None) -> str:
    """Generate ATAC-seq analysis message using ATAC toolset"""
    
    if folder_path:
        folder_path = Path(folder_path).resolve()
        
        message = f"""🧬 ATAC-seq Analysis Pipeline for: {folder_path}

You have access to both ATAC-seq toolset and TodoList management. Follow this intelligent workflow:

🧬 PHASE 0: INTELLIGENT SPECIES DETECTION & COMPREHENSIVE RESOURCE SETUP

Smart species detection and resource management workflow:
1. First try: atac.auto_detect_species("{folder_path}") 
   - Auto-detect from folder names, file names, and FASTQ headers
   - Returns suggested species and genome version with confidence score

2. Confidence-based decision:
   - If high confidence (≥2.0): Proceed with detected species
   - If medium confidence (1.0-2.0): Ask user to confirm species
   - If low confidence (<1.0): Ask user to specify species

3. Then set up ALL genome resources with organized structure:
   - COMPREHENSIVE: atac.setup_genome_resources(species, genome_version, include_gtf=True, include_blacklist=True)
     * Downloads genome FASTA + BWA index
     * Downloads GTF annotations (GENCODE/ENSEMBL)  
     * Downloads ENCODE blacklist regions
     * Organizes in: reference/genome/species/, reference/gtf/species/, reference/blacklist/species/
     * Smart caching - skips existing files
     * Auto-selects fastest download sources
   
   - QUICK TEST: atac.setup_genome_resources("human", "hg38_test") - Single chromosome for testing
   - MANUAL MGMT: atac.list_available_resources() - Show what's already downloaded
   
4. Resource validation and management:
   - atac.check_genome_integrity(species, genome_version) - Verify file integrity
   - atac.clean_incomplete_downloads() - Clean up corrupted files
   - atac.get_resource_info(species, genome_version) - Detailed resource info

📋 PHASE 1: SMART TODO CREATION BASED ON DATA SCAN

Second, scan the data to understand what we have:
1. Call: atac.scan_folder("{folder_path}")
2. Based on scan results, create specific ATAC-seq todos using add_todo():

IF scan shows RAW FASTQ files:
- add_todo("ATAC-seq Quality Control with FastQC")  
- add_todo("ATAC-seq Adapter Trimming with Trim Galore")
- add_todo("ATAC-seq Genome Alignment with Bowtie2")  # Optimized for ATAC-seq
- add_todo("ATAC-seq BAM Filtering and Duplicate Removal")
- add_todo("ATAC-seq Peak Calling with MACS2")
- add_todo("ATAC-seq Coverage Track Generation") 
- add_todo("ATAC-seq QC Report Generation")

IF scan shows BAM files:
- add_todo("ATAC-seq BAM Quality Assessment")
- add_todo("ATAC-seq Peak Calling with MACS2")
- add_todo("ATAC-seq Coverage Track Generation")
- add_todo("ATAC-seq QC Report Generation")

IF scan shows PEAK files:
- add_todo("ATAC-seq Peak Annotation and Analysis")
- add_todo("ATAC-seq Motif Enrichment Analysis") 
- add_todo("ATAC-seq Final Report Generation")

📊 PHASE 2: EXECUTE WITH TODO TRACKING

For each TODO task:
1. Use execute_current_task() to get smart guidance
2. Run the appropriate ATAC tool (with rich console output)
3. **AUTO-HANDLE DEPENDENCIES**: If BWA alignment fails due to missing reference genome, automatically call atac.setup_reference_genome("hg38") first
4. Call mark_task_done("detailed description of what was completed")
5. Use show_todos() to display progress

🔄 PHASE 3: ADAPTIVE TODO REFINEMENT

As analysis progresses:
- If dependencies are missing → add_todo("Install missing ATAC-seq tools")
- If quality issues found → add_todo("Address data quality issues")  
- If additional analysis needed → add_todo("Additional analysis task")
- Update todos based on results from each step

🎯 EXECUTION STRATEGY:

1. START: atac.scan_folder("{folder_path}") 
2. CREATE todos based on scan results
3. BEGIN todo execution loop:
   - show_todos() to see current status
   - execute_current_task() for guidance
   - Run ATAC tool with rich output
   - mark_task_done() with detailed completion notes
   - Repeat until all todos complete

💡 KEY BENEFITS:
- TodoList adapts to your specific data
- Track progress through complex ATAC-seq pipeline  
- Rich visual output from ATAC tools
- Smart guidance at each step

START NOW: Scan folder and create intelligent todos!"""
        
    else:
        message = """I need help with ATAC-seq analysis using your specialized toolsets.

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