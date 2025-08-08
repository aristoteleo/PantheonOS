"""Simplified ATAC-seq mode handler"""

from pathlib import Path
from typing import Optional


def generate_atac_cellranger_message(folder_path: str) -> str:
    """Generate Cell Ranger ATAC analysis message with auto-detection"""
    folder_path = Path(folder_path).resolve()
    
    return f"""🧬 Cell Ranger ATAC Analysis Pipeline for: {folder_path}

🔍 AUTOMATIC FOLDER ANALYSIS:

I'll automatically analyze the folder contents to determine the appropriate Cell Ranger ATAC workflow:

1. **File Detection**: Scan for FASTQ files, BCL files, or existing Cell Ranger outputs
2. **Data Type Detection**: Determine if data is:
   - Raw BCL files (need mkfastq first)
   - 10x FASTQ files (ready for count)
   - Existing Cell Ranger outputs (ready for analysis/aggregation)
3. **Reference Genome Setup**: Auto-detect species or use user preference
4. **Smart Workflow Selection**: Choose the optimal Cell Ranger ATAC pipeline

🚀 AUTOMATED WORKFLOW SELECTION:

**OPTION A: BCL → FASTQ → Count Pipeline**
If BCL files detected:
- cellranger_atac.mkfastq() - Demultiplex BCL to FASTQ
- cellranger_atac.count() - Run complete analysis pipeline

**OPTION B: Direct Count Pipeline**  
If 10x FASTQ files detected:
- cellranger_atac.validate_fastq_10x() - Validate file format
- cellranger_atac.count() - Run complete analysis pipeline

**OPTION C: Multi-sample Aggregation**
If multiple sample outputs detected:
- cellranger_atac.create_aggr_csv() - Prepare aggregation file
- cellranger_atac.aggr() - Aggregate samples for comparison

**OPTION D: Analysis/QC Only**
If Cell Ranger outputs already exist:
- cellranger_atac.scan_run_outputs() - Analyze existing results
- cellranger_atac.validate_run_quality() - Quality assessment

🎯 KEY AUTOMATION FEATURES:

- **Smart Species Detection**: Auto-detect from folder names and file content
- **Reference Auto-Setup**: Download/build appropriate reference genome
- **Quality Validation**: Automatic FASTQ and depth validation (25K-50K reads/cell)
- **Progress Tracking**: TodoList integration for pipeline monitoring
- **Error Handling**: Smart retry and troubleshooting suggestions

📋 TODO WORKFLOW (Cell Ranger ATAC Specific):

I'll automatically create appropriate todos based on scATAC-seq workflow:

**For BCL Files (Raw Sequencer Output):**
0. add_todo("Check Cell Ranger ATAC installation")
1. add_todo("Setup Cell Ranger ATAC reference genome")  
2. add_todo("Run Cell Ranger ATAC mkfastq (BCL to FASTQ)")
3. add_todo("Run Cell Ranger ATAC count pipeline")
4. add_todo("Analyze Cell Ranger ATAC outputs (web_summary.html)")

**For 10x FASTQ Files (I1, R1, R2, R3):**
0. add_todo("Check Cell Ranger ATAC installation")
1. add_todo("Setup Cell Ranger ATAC reference genome")
2. add_todo("Validate 10x scATAC-seq FASTQ files")
3. add_todo("Run Cell Ranger ATAC count pipeline")  
4. add_todo("Analyze Cell Ranger ATAC outputs (web_summary.html)")

**For Completed Cell Ranger ATAC Runs:**
1. add_todo("Analyze existing Cell Ranger ATAC outputs")
2. add_todo("Create aggregation CSV for multi-sample comparison")
3. add_todo("Run Cell Ranger ATAC aggr (if multiple samples)")
4. add_todo("Prepare data for downstream scATAC-seq analysis")

🔄 EXECUTION FLOW:

```
Start: cellranger_atac.scan_folder_and_detect_workflow("{folder_path}")
  ↓
Auto-detect: BCL/FASTQ files, validate 10x format
  ↓
Create todos: Based on detected workflow (BCL→mkfastq→count vs direct count)
  ↓
Execute pipeline: cellranger-atac commands with proper parameters
  ↓
Analyze outputs: web_summary.html, fragments.tsv.gz, filtered matrices
```

🎯 KEY scATAC-seq WORKFLOW STEPS:

1. **File Detection**: Identify BCL vs 10x FASTQ files (I1/R1/R2/R3)
2. **Reference Setup**: Download/validate Cell Ranger ATAC reference
3. **Pipeline Execution**: 
   - BCL path: mkfastq → count
   - FASTQ path: direct count
4. **Output Analysis**: Use Cell Ranger ATAC's built-in QC reports

START NOW: Let me analyze your folder and create the scATAC-seq workflow!

To begin, I'll run: cellranger_atac.scan_folder_and_detect_workflow("{folder_path}")"""


def generate_atac_analysis_message(folder_path: Optional[str] = None) -> str:
    """Generate ATAC-seq analysis message using ATAC toolset"""
    
    if folder_path:
        folder_path = Path(folder_path).resolve()
        
        message = f"""🧬 ATAC-seq Analysis Pipeline for: {folder_path}

You have access to BOTH bulk ATAC-seq and single-cell ATAC-seq (10x Genomics Cell Ranger ATAC) toolsets, plus TodoList management. Choose your analysis approach:

🎯 WORKFLOW SELECTION:

**OPTION A: 10x Genomics Single-Cell ATAC-seq (Cell Ranger ATAC)**
Use if you have: 10x Genomics FASTQ files, BCL files, or single-cell ATAC-seq data

KEY TOOLS:
- cellranger_atac.init() - Initialize Cell Ranger ATAC project
- cellranger_atac.count() - Main analysis pipeline (FASTQ → peaks, matrices, reports)
- cellranger_atac.aggr() - Aggregate multiple samples for comparative analysis
- cellranger_atac.mkref() - Build custom reference genome
- cellranger_atac.mkfastq() - Demultiplex BCL files
- cellranger_atac.download_reference() - Download pre-built references (human/mouse)
- cellranger_atac.auto_download_and_build_reference() - Auto-build custom organism references
- cellranger_atac.list_supported_organisms() - Show organisms for auto-building
- cellranger_atac.validate_fastq_10x() - Validate FASTQ files for 10x compatibility
- cellranger_atac.validate_sequencing_depth() - Check sequencing depth (25K-50K reads/cell)
- cellranger_atac.validate_run_quality() - Quality assessment from summary metrics
- cellranger_atac.check_installation() - Verify Cell Ranger ATAC setup
- cellranger_atac.scan_run_outputs() - Analyze completed runs

**OPTION B: Bulk/Standard ATAC-seq (Traditional Pipeline)**
Use for: Traditional ATAC-seq samples, bulk samples, or when you want detailed control

🧬 PHASE 0: INTELLIGENT SPECIES DETECTION & COMPREHENSIVE RESOURCE SETUP (Bulk Pipeline)

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
     * Downloads genome FASTA + Bowtie2 index (ATAC-seq optimized)
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

📋 PHASE 1: INTELLIGENT TODO CREATION (STRICT DUPLICATE PREVENTION)

MANDATORY STEPS - NO EXCEPTIONS:
1. FIRST: Call show_todos() to see existing todos
2. SECOND: Call atac.scan_folder("{folder_path}")
3. THIRD: Check if ANY ATAC-seq todos already exist

DUPLICATE PREVENTION LOGIC:
- If show_todos() shows ANY todo containing "ATAC-seq", "FastQC", "Alignment", "Peak" -> SKIP creation completely
- If todo list is NOT EMPTY -> SKIP todo creation, work with existing todos instead
- ONLY create todos if the todo list is COMPLETELY EMPTY

TODO CREATION (ONLY IF LIST IS EMPTY):

**For Cell Ranger ATAC (scATAC-seq):**
  0. add_todo("Check Cell Ranger ATAC installation and reference")
  1. add_todo("Validate 10x scATAC-seq files (I1/R1/R2/R3 or BCL)")
  2. add_todo("Run Cell Ranger ATAC mkfastq (if BCL files)")
  3. add_todo("Run Cell Ranger ATAC count (25K-50K reads/cell)")
  4. add_todo("Review Cell Ranger ATAC QC outputs (web_summary.html)")

**For Bulk ATAC-seq (Traditional):**
  0. add_todo("Setup reference genome automatically for Bowtie2 indexing")
  1. add_todo("ATAC-seq Quality Control with FastQC")  
  2. add_todo("ATAC-seq Adapter Trimming with Trim Galore") 
  3. add_todo("ATAC-seq Genome Alignment with Bowtie2")
  4. add_todo("ATAC-seq BAM Filtering (no duplicate removal)")
  5. add_todo("ATAC-seq Peak Calling with MACS2")
  6. add_todo("ATAC-seq Coverage Track Generation") 
  7. add_todo("ATAC-seq QC Report Generation")

🚨 ABSOLUTE RULE: If todo list has ANY items, DO NOT CREATE NEW TODOS!

📊 PHASE 2: EXECUTE WITH TODO TRACKING

For each TODO task:
1. Use execute_current_task() to get smart guidance
2. Run the appropriate ATAC tool (with rich console output)
3. **AUTO-HANDLE EVERYTHING**: Fully automated pipeline:
   - Auto-install missing tools (bowtie2, samtools, etc.)
   - Auto-detect FASTQ files for alignment (R1/R2 pairs)
   - Auto-generate appropriate output names
   - Auto-proceed without manual input
4. Call mark_task_done("detailed description of what was completed")
5. Use show_todos() to display progress

🔄 PHASE 3: ADAPTIVE TODO REFINEMENT

As analysis progresses:
- If dependencies are missing → add_todo("Install missing ATAC-seq tools")
- If quality issues found → add_todo("Address data quality issues")  
- If additional analysis needed → add_todo("Additional analysis task")
- Update todos based on results from each step

🎯 EXECUTION STRATEGY (AUTOMATED):

1. START: atac.scan_folder("{folder_path}") for bulk OR cellranger_atac.scan_folder_and_detect_workflow() for scATAC
2. CHECK: show_todos() - avoid creating duplicates  
3. CREATE todos ONCE based on data type detection
4. AUTOMATED EXECUTION:
   - **For scATAC-seq (10x)**: Use cellranger_atac tools
     * cellranger_atac.check_installation()
     * cellranger_atac.download_reference() or setup custom reference  
     * cellranger_atac.count() with proper 10x parameters
   - **For bulk ATAC-seq**: Use traditional atac tools
     * atac.auto_align_fastq() - fully automated alignment
     * atac.process_bam_smart() - filters BAM, skips duplicate removal
   - mark_task_done() with detailed completion notes
   - Repeat until all todos complete

💡 KEY AUTOMATION TOOLS:
- atac.auto_align_fastq() - Auto-detects FASTQ, installs tools, runs alignment
- atac.install_missing_tools() - Auto-installs any missing dependencies

💡 KEY BENEFITS:
- TodoList adapts to your specific data
- Track progress through complex ATAC-seq pipeline  
- Rich visual output from ATAC tools
- Smart guidance at each step

START NOW: Scan folder and create intelligent todos!

🎯 **COMMAND STRUCTURE SUMMARY:**

**Cell Ranger ATAC (Auto-Detection):**
- `/atac cellranger <folder>` - Auto-analyze folder and run optimal Cell Ranger ATAC workflow

**Traditional ATAC-seq (Bulk):**
- `/atac init` - Enter ATAC-seq mode (simple prompt loading)
- `/atac upstream <folder>` - Run upstream analysis on specific folder

**Direct Tool Usage:**
- cellranger_atac.scan_folder_and_detect_workflow("./folder") - Smart detection and workflow
- cellranger_atac.count(run_id="sample1", fastqs="./fastqs", reference="./ref")
- atac.scan_folder("./data") for bulk analysis"""
        
    else:
        message = """I need help with ATAC-seq analysis using your specialized toolsets.

You have access to comprehensive ATAC-seq toolsets for BOTH bulk and single-cell analysis, plus TODO management tools:

📋 TODO MANAGEMENT (use these for ALL tasks):
- add_todo() - Add tasks and auto-break them down
- show_todos() - Display current progress  
- execute_current_task() - Get smart guidance
- mark_task_done() - Mark tasks complete and progress

🧬 CELL RANGER ATAC TOOLSET (10x Genomics Single-Cell):
SETUP & INSTALLATION:
- cellranger_atac.check_installation() - Verify Cell Ranger ATAC setup
- cellranger_atac.init() - Initialize Cell Ranger ATAC project
- cellranger_atac.list_commands() - Show available commands
- cellranger_atac.download_reference() - Download pre-built references (human/mouse)
- cellranger_atac.list_available_references() - Show pre-built references
- cellranger_atac.auto_download_and_build_reference() - Auto-build custom organisms
- cellranger_atac.list_supported_organisms() - Show supported organisms

DATA VALIDATION:
- cellranger_atac.validate_fastq_10x() - Validate FASTQ naming and structure
- cellranger_atac.validate_sequencing_depth() - Check sequencing depth (25K-50K reads/cell)

MAIN ANALYSIS:
- cellranger_atac.count() - Run complete Cell Ranger ATAC pipeline
- cellranger_atac.aggr() - Aggregate multiple samples
- cellranger_atac.create_aggr_csv() - Create aggregation CSV file
- cellranger_atac.mkref() - Build custom reference genome package
- cellranger_atac.mkfastq() - Demultiplex BCL files to FASTQ
- cellranger_atac.testrun() - Run test dataset to verify installation

QUALITY CONTROL & ANALYSIS:
- cellranger_atac.validate_run_quality() - Comprehensive quality assessment
- cellranger_atac.scan_run_outputs() - Analyze completed runs

🧬 BULK ATAC-seq TOOLSET (Traditional):
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

🎯 GETTING STARTED:
1. First determine your data type:
   - 10x Genomics FASTQ files with cell barcodes → Use cellranger_atac toolset
   - Traditional bulk ATAC-seq FASTQ files → Use atac toolset
   
2. Check installation: cellranger_atac.check_installation() OR atac.check_dependencies()

3. Add todos for your specific analysis type and start your ATAC-seq pipeline!

Examples:
- Single-cell (pre-built): cellranger_atac.download_reference('human', 'GRCh38')
- Single-cell (custom): cellranger_atac.auto_download_and_build_reference('zebrafish', 'GRCz11')
- Single-cell analysis: cellranger_atac.count(run_id="sample1", fastqs="./fastqs", reference="./ref")
- Bulk ATAC-seq: atac.scan_folder("./data") then follow automated pipeline

Please start by adding a todo for your ATAC-seq analysis task, then use the appropriate ATAC tools!"""
    
    return message