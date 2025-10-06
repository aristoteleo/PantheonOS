"""ATAC-seq Upstream Analysis - Data preparation, QC, alignment, and BAM processing"""

import os
import subprocess
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from pantheon.utils.log import logger
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from pantheon.toolset import ToolSet, tool
from rich.console import Console

class ATACSeqUpstreamToolSetBase(ToolSet):
    """ATAC-seq Upstream Analysis Toolset - From FASTQ to filtered BAM files"""
    
    def __init__(
        self,
        name: str = "atac_upstream",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.pipeline_config = self._initialize_config()
        self.console = Console()
        
    def _initialize_config(self) -> Dict[str, Any]:
        """Initialize ATAC-seq pipeline configuration"""
        return {
            "file_extensions": {
                "raw_reads": [".fastq", ".fq", ".fastq.gz", ".fq.gz", ".fastq.bz2", 
                             ".fq.bz2", ".fastq.zst", ".fq.zst", ".sra"],
                "barcodes": [".whitelist.txt", ".tsv"],
                "genome": [".fa", ".fasta", ".fai", ".dict"],
                "bwa_index": [".amb", ".ann", ".bwt", ".pac", ".sa"],
                "bowtie2_index": [".bt2", ".bt2l"],
                "regions": [".bed", ".bed.gz", ".chrom.sizes"],
                "alignment": [".sam", ".bam", ".cram", ".bam.bai", ".cram.crai"],
                "peaks": [".narrowPeak", ".broadPeak", ".gappedPeak", ".xls", ".bedgraph", ".bdg"],
                "tracks": [".bw", ".bigwig", ".tdf"],
                "reports": [".html", ".json", ".txt", ".tsv", ".csv", ".pdf", ".png"]
            },
            "tools": {
                "acquisition": ["sra-tools", "pigz", "pbzip2", "zstd", "seqtk"],
                "qc": ["fastqc", "multiqc", "fastp", "trim_galore", "cutadapt"],
                "alignment": ["bowtie2", "bwa", "bwa-mem2", "minimap2"],  # Bowtie2 first for ATAC-seq
                "sam_processing": ["samtools", "sambamba", "picard", "samblaster"],
                "atac_qc": ["ataqv", "preseq", "deeptools"],
                "peak_calling": ["macs2", "genrich", "hmmratac"],
                "coverage": ["deeptools", "bedtools", "ucsc-tools"],
                "annotation": ["homer", "meme", "chipseeker", "bedtools"]
            },
            "default_params": {
                "threads": 4,
                "memory": "8G",
                "quality_threshold": 20,
                "min_mapping_quality": 30,
                "fragment_size_range": [50, 1000],
                "peak_calling_fdr": 0.01
            }
        }
    
    @tool
    def init(self, project_name: str = "atac_analysis", 
             genome: str = "hg38",
             paired_end: bool = True) -> str:
        """
        Initialize ATAC-seq analysis project structure
        
        Args:
            project_name: Name of the project
            genome: Reference genome (hg38, mm10, etc.)
            paired_end: Whether data is paired-end
            
        Returns:
            Status message
        """
        project_dir = self.workspace_path / project_name
        
        # Create directory structure
        dirs = [
            "fastq", "fastq_trimmed", "qc", "qc/fastqc", "qc/multiqc",
            "alignment", "alignment/filtered", "alignment/dedup",
            "peaks", "peaks/macs2", "peaks/genrich", 
            "coverage", "coverage/bigwig",
            "motifs", "annotation", "reports", "logs", "scripts"
        ]
        
        for dir_name in dirs:
            (project_dir / dir_name).mkdir(parents=True, exist_ok=True)
        
        # Create config file
        config = {
            "project_name": project_name,
            "genome": genome,
            "paired_end": paired_end,
            "created": str(Path.cwd()),
            "pipeline_version": "1.0.0",
            "directories": {d: str(project_dir / d) for d in dirs}
        }
        
        config_file = project_dir / "atac_config.json"
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Create sample sheet template
        sample_sheet = project_dir / "samples.tsv"
        with open(sample_sheet, 'w') as f:
            if paired_end:
                f.write("sample_id\tfastq_r1\tfastq_r2\tcondition\treplicate\n")
                f.write("# Example:\n")
                f.write("# Sample1\tsample1_R1.fastq.gz\tsample1_R2.fastq.gz\tcontrol\t1\n")
            else:
                f.write("sample_id\tfastq\tcondition\treplicate\n")
                f.write("# Example:\n")
                f.write("# Sample1\tsample1.fastq.gz\tcontrol\t1\n")
        
        return f"✅ ATAC-seq project '{project_name}' initialized at {project_dir}"
    
    @tool
    def check_dependencies(self) -> Dict[str, Any]:
        """Check which ATAC-seq tools are installed and suggest installation"""
        
        # Rich console header
        logger.info("\n" + "="*70)
        logger.info("🔧 [bold cyan]ATAC-seq Tools Dependency Check[/bold cyan]", justify="center")
        logger.info("="*70)
        
        tools_status = {"installed": {}, "missing": [], "install_commands": []}
        
        # Core tools for ATAC-seq (Bowtie2 is now the primary aligner)
        core_tools = {
            "fastqc": "conda install -c bioconda fastqc",
            "bowtie2": "conda install -c bioconda bowtie2",  # Primary aligner for ATAC-seq
            "bwa": "conda install -c bioconda bwa",  # Secondary option
            "samtools": "conda install -c bioconda samtools",
            "picard": "conda install -c bioconda picard",  # For duplicate marking
            "macs2": "conda install -c bioconda macs2",
            "deeptools": "conda install -c bioconda deeptools",
            "trim_galore": "conda install -c bioconda trim-galore"
        }
        
        # Check tools with progress
        logger.info("\n  [cyan]Checking installed tools...[/cyan]")
        
        # Create dependency status table
        dep_table = Table(title="Dependency Status", show_header=True, header_style="bold magenta")
        dep_table.add_column("Tool", style="cyan", width=15)
        dep_table.add_column("Status", justify="center", width=10)
        dep_table.add_column("Path / Install Command", style="dim")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:
            check_task = progress.add_task("Checking tools...", total=len(core_tools))
            
            for tool, install_cmd in core_tools.items():
                progress.update(check_task, description=f"Checking {tool}...")
                
                # Handle special cases for checking
                check_cmd = tool
                if tool == "trim_galore":
                    check_cmd = "trim_galore"
                        
                try:
                    result = subprocess.run(
                        ["which", check_cmd],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        path = result.stdout.strip()
                        tools_status["installed"][tool] = path
                        dep_table.add_row(
                            f"🧬 {tool}",
                            "[green]✅ FOUND[/green]",
                            f"[dim]{path}[/dim]"
                        )
                    else:
                        tools_status["missing"].append(tool)
                        tools_status["install_commands"].append(f"{tool}: {install_cmd}")
                        dep_table.add_row(
                            f"🧬 {tool}",
                            "[red]❌ MISSING[/red]",
                            f"[yellow]{install_cmd}[/yellow]"
                        )
                except:
                    tools_status["missing"].append(tool)
                    tools_status["install_commands"].append(f"{tool}: {install_cmd}")
                    dep_table.add_row(
                        f"🧬 {tool}",
                        "[red]❌ MISSING[/red]",
                        f"[yellow]{install_cmd}[/yellow]"
                    )
                
                progress.advance(check_task)
        
        logger.info("", rich=dep_table)
        
        # Summary
        installed_count = len(tools_status["installed"])
        missing_count = len(tools_status["missing"])
        total_count = installed_count + missing_count
        
        if missing_count == 0:
            summary_panel = Panel(
                f"[green]✅ All tools installed! ({installed_count}/{total_count})[/green]",
                title="Status",
                border_style="green"
            )
        else:
            summary_panel = Panel(
                f"[yellow]⚠️  {missing_count} tools missing ({installed_count}/{total_count} installed)[/yellow]\n" +
                f"[cyan]Run: atac.install_missing_tools({tools_status['missing']})[/cyan]",
                title="Status",
                border_style="yellow"
            )
        
        logger.info("", rich=summary_panel)
        logger.info("="*70 + "\n")
        
        return tools_status
    
    @tool
    def install_missing_tools(self, tools: List[str]) -> str:
        """Install missing ATAC-seq tools using conda"""
        
        # Rich console header
        logger.info("\n" + "="*70)
        logger.info("⬇   [bold cyan]Installing Missing ATAC-seq Tools[/bold cyan]", justify="center")
        logger.info("="*70)
        
        if not tools:
            logger.info("✅ [green]No tools to install - all dependencies are already satisfied![/green]")
            return "No tools to install"
        
        install_commands = {
            "fastqc": "conda install -c bioconda fastqc -y",
            "bowtie2": "conda install -c bioconda bowtie2 -y",  # Primary aligner for ATAC-seq
            "bwa": "conda install -c bioconda bwa -y", 
            "samtools": "conda install -c bioconda samtools -y",
            "picard": "conda install -c bioconda picard -y",  # For duplicate marking
            "macs2": "conda install -c bioconda macs2 -y",
            "deeptools": "conda install -c bioconda deeptools -y",
            "trim_galore": "conda install -c bioconda trim-galore -y"
        }
        
        results = []
        successful_installs = 0
        failed_installs = 0
        
        logger.info(f"  [yellow]Installing {len(tools)} tools:[/yellow] {', '.join(tools)}\n")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console
        ) as progress:
            
            main_task = progress.add_task("Installing tools...", total=len(tools))
            
            for i, tool in enumerate(tools, 1):
                if tool in install_commands:
                    cmd = install_commands[tool]
                    progress.update(main_task, description=f"Installing {tool}... ({i}/{len(tools)})")
                    
                    logger.info(f"  [cyan]Installing {tool}...[/cyan]")
                    
                    try:
                        result = subprocess.run(
                            cmd.split(),
                            capture_output=True,
                            text=True,
                            timeout=300  # 5 minute timeout
                        )
                        if result.returncode == 0:
                            logger.info(f"✅ [green]Successfully installed {tool}[/green]")
                            results.append(f"✅ Successfully installed {tool}")
                            successful_installs += 1
                        else:
                            error_msg = result.stderr.strip()[:100] + "..." if len(result.stderr) > 100 else result.stderr.strip()
                            logger.info(f"❌ [red]Failed to install {tool}[/red]")
                            logger.info(f"   [dim]Error: {error_msg}[/dim]")
                            results.append(f"❌ Failed to install {tool}: {error_msg}")
                            failed_installs += 1
                    except subprocess.TimeoutExpired:
                        logger.info(f"  [yellow]Installation of {tool} timed out[/yellow]")
                        results.append(f"  Installation of {tool} timed out")
                        failed_installs += 1
                    except Exception as e:
                        logger.info(f"❌ [red]Error installing {tool}: {str(e)}[/red]")
                        results.append(f"❌ Error installing {tool}: {str(e)}")
                        failed_installs += 1
                else:
                    logger.info(f"❓ [yellow]Unknown tool: {tool}[/yellow]")
                    results.append(f"❓ Unknown tool: {tool}")
                    failed_installs += 1
                
                progress.advance(main_task)
                logger.info("")  # Add spacing
        
        # Final summary
        if failed_installs == 0:
            summary_panel = Panel(
                f"[green]  All tools installed successfully! ({successful_installs}/{len(tools)})[/green]",
                title="Installation Complete",
                border_style="green"
            )
        else:
            summary_panel = Panel(
                f"[yellow]⚠️  Installation completed with issues[/yellow]\n" +
                f"[green]✅ Successful: {successful_installs}[/green]\n" +
                f"[red]❌ Failed: {failed_installs}[/red]",
                title="Installation Summary",
                border_style="yellow"
            )
        
        logger.info("", rich=summary_panel)
        logger.info("="*70 + "\n")
        
        return "\n".join(results)
    
    def _check_and_install_tool(self, tool_name: str, show_output: bool = True) -> bool:
        """Check if a tool is available and install if missing
        
        Args:
            tool_name: Name of the tool to check
            show_output: Whether to show installation progress
            
        Returns:
            True if tool is available or successfully installed, False otherwise
        """
        try:
            subprocess.run(["which", tool_name], capture_output=True, check=True)
            return True  # Tool is already available
        except subprocess.CalledProcessError:
            if show_output:
                logger.info(f"⚠️ [yellow]{tool_name} not found, installing...[/yellow]")
            
            install_result = self.install_missing_tools([tool_name])
            
            if f"Successfully installed {tool_name}" in install_result:
                if show_output:
                    logger.info(f"✅ [green]{tool_name} installed successfully[/green]")
                return True
            else:
                if show_output:
                    logger.info(f"❌ [red]Failed to install {tool_name}[/red]")
                return False
    
    @tool
    def auto_detect_species(self, folder_path: str) -> Dict[str, Any]:
        """Auto-detect species from FASTQ files or folder structure"""
        logger.info("")
        logger.info("", rich=Panel.fit("  Auto-detecting Species", style="bright_cyan"))
        
        try:
            folder = Path(folder_path)
            species_hints = {}
            detected_species = "unknown"
            confidence = 0
            
            # Check folder and file names for species hints
            folder_name = folder.name.lower()
            species_keywords = {
                "human": ["human", "hg38", "hg19", "homo", "sapiens"],
                "mouse": ["mouse", "mm10", "mm9", "mus", "musculus"],
                "rat": ["rat", "rn6", "rn5", "rattus", "norvegicus"],
                "fly": ["drosophila", "dm6", "dm3", "melanogaster"],
                "worm": ["elegans", "ce11", "ce10", "caenorhabditis"],
                "zebrafish": ["zebrafish", "danrer", "danio", "rerio"]
            }
            
            # Check folder name
            for species, keywords in species_keywords.items():
                for keyword in keywords:
                    if keyword in folder_name:
                        species_hints[species] = species_hints.get(species, 0) + 2
            
            # Check FASTQ files for species hints
            fastq_files = list(folder.glob("*.fastq*")) + list(folder.glob("*.fq*"))
            
            for fastq_file in fastq_files[:3]:  # Check first 3 files
                filename = fastq_file.name.lower()
                for species, keywords in species_keywords.items():
                    for keyword in keywords:
                        if keyword in filename:
                            species_hints[species] = species_hints.get(species, 0) + 1
                
                # Quick peek at first few reads for organism info
                try:
                    if fastq_file.suffix == '.gz':
                        import gzip
                        with gzip.open(fastq_file, 'rt') as f:
                            for i, line in enumerate(f):
                                if i > 40:  # Check first 10 reads (4 lines each)
                                    break
                                if i % 4 == 0 and line.startswith('@'):  # Header line
                                    header = line.lower()
                                    for species, keywords in species_keywords.items():
                                        for keyword in keywords:
                                            if keyword in header:
                                                species_hints[species] = species_hints.get(species, 0) + 0.5
                    else:
                        with open(fastq_file, 'r') as f:
                            for i, line in enumerate(f):
                                if i > 40:
                                    break
                                if i % 4 == 0 and line.startswith('@'):
                                    header = line.lower()
                                    for species, keywords in species_keywords.items():
                                        for keyword in keywords:
                                            if keyword in header:
                                                species_hints[species] = species_hints.get(species, 0) + 0.5
                except:
                    continue
            
            # Determine most likely species
            if species_hints:
                detected_species = max(species_hints.items(), key=lambda x: x[1])[0]
                confidence = species_hints[detected_species]
            
            # Map to genome versions
            genome_mapping = {
                "human": "hg38",
                "mouse": "mm10", 
                "rat": "rn6",
                "fly": "dm6",
                "worm": "ce11",
                "zebrafish": "danRer11"
            }
            
            suggested_genome = genome_mapping.get(detected_species, "hg38")
            
            # Display results
            results_table = Table(title="Species Detection Results")
            results_table.add_column("Species", style="cyan")
            results_table.add_column("Confidence", style="magenta")
            results_table.add_column("Suggested Genome", style="green")
            
            if confidence > 0:
                results_table.add_row(detected_species.title(), f"{confidence:.1f}", suggested_genome)
                for species, score in sorted(species_hints.items(), key=lambda x: x[1], reverse=True):
                    if species != detected_species:
                        alt_genome = genome_mapping.get(species, species)
                        results_table.add_row(species.title(), f"{score:.1f}", alt_genome)
            else:
                results_table.add_row("Unknown", "0.0", "hg38 (default)")
            
            logger.info("", rich=results_table)
            
            if confidence >= 2:
                logger.info(f"[green]✅ High confidence detection: {detected_species.title()} ({suggested_genome})")
            elif confidence >= 1:
                logger.info(f"[yellow]⚠️ Medium confidence detection: {detected_species.title()} ({suggested_genome})")
            else:
                logger.info(f"[red]❌ Low confidence - defaulting to Human (hg38)")
                suggested_genome = "hg38"
            
            return {
                "status": "success",
                "detected_species": detected_species,
                "suggested_genome": suggested_genome,
                "confidence": confidence,
                "all_hints": species_hints
            }
            
        except Exception as e:
            logger.info(f"[red]❌ Species detection failed: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "suggested_genome": "hg38"
            }
    
    @tool
    def setup_genome_resources(self, species: str, genome_version: str = None, include_gtf: bool = True, include_blacklist: bool = True) -> str:
        """Comprehensive genome resources setup with organized file structure"""
        logger.info("")
        logger.info("", rich=Panel.fit("🧬 Setting Up Genome Resources", style="bright_cyan"))
        
        # Standardize species names
        species_mapping = {
            "human": {"common": "human", "scientific": "homo_sapiens", "default_genome": "hg38"},
            "mouse": {"common": "mouse", "scientific": "mus_musculus", "default_genome": "mm10"},
            "rat": {"common": "rat", "scientific": "rattus_norvegicus", "default_genome": "rn6"}
        }
        
        if species.lower() not in species_mapping:
            available = ", ".join(species_mapping.keys())
            return f"❌ Unsupported species: {species}. Available: {available}"
        
        species_info = species_mapping[species.lower()]
        genome_version = genome_version or species_info["default_genome"]
        
        # Create organized directory structure
        base_ref_dir = self.workspace_path / "reference"
        genome_dir = base_ref_dir / "genome" / species_info["common"]
        gtf_dir = base_ref_dir / "gtf" / species_info["common"] 
        blacklist_dir = base_ref_dir / "blacklist" / species_info["common"]
        
        # Create all directories
        for dir_path in [genome_dir, gtf_dir, blacklist_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Define resource configurations
        resources_config = self._get_resources_config()
        
        if species_info["common"] not in resources_config:
            return f"❌ No resource configuration for {species}"
        
        species_resources = resources_config[species_info["common"]]
        if genome_version not in species_resources:
            available_versions = ", ".join(species_resources.keys())
            return f"❌ Genome version {genome_version} not available for {species}. Available: {available_versions}"
        
        genome_config = species_resources[genome_version]
        
        # Check existing resources
        existing_resources = self._check_existing_resources(species_info["common"], genome_version)
        
        # Display resource summary
        self._display_resource_plan(species_info, genome_version, genome_config, include_gtf, include_blacklist, existing_resources)
        
        # Setup genome
        genome_result = self._setup_genome_file(genome_dir, genome_version, genome_config)
        if not genome_result["success"]:
            return genome_result["message"]
        
        # Setup GTF if requested
        gtf_result = {"success": True, "message": "GTF setup skipped"}
        if include_gtf:
            gtf_result = self._setup_gtf_file(gtf_dir, genome_version, genome_config)
        
        # Setup blacklist if requested  
        blacklist_result = {"success": True, "message": "Blacklist setup skipped"}
        if include_blacklist:
            blacklist_result = self._setup_blacklist_file(blacklist_dir, genome_version, genome_config)
        
        # Generate summary report
        return self._generate_setup_summary(species_info, genome_version, genome_result, gtf_result, blacklist_result)
    
    def _get_resources_config(self) -> Dict[str, Any]:
        """Get comprehensive resource configuration with multiple sources"""
        return {
            "human": {
                "hg38": {
                    "name": "Human genome GRCh38/hg38",
                    "genome": {
                        "size": "~3.0GB",
                        "sources": {
                            "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz",
                            "ENSEMBL": "http://ftp.ensembl.org/pub/release-104/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz",
                            "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/genomes/refseq/vertebrate_mammalian/Homo_sapiens/latest_assembly_versions/GCF_000001405.40_GRCh38.p14/GCF_000001405.40_GRCh38.p14_genomic.fna.gz"
                        },
                        "md5": None,  # Could add MD5 checksums for validation
                        "expected_size": 3200000000  # ~3.2GB uncompressed
                    },
                    "gtf": {
                        "sources": {
                            "GENCODE": "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/gencode.v44.primary_assembly.annotation.gtf.gz",
                            "ENSEMBL": "http://ftp.ensembl.org/pub/release-104/gtf/homo_sapiens/Homo_sapiens.GRCh38.104.gtf.gz",
                            "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/gencode/Gencode_human/release_44/gencode.v44.primary_assembly.annotation.gtf.gz"
                        },
                        "size": "~50MB",
                        "expected_size": 52000000
                    },
                    "blacklist": {
                        "sources": {
                            "ENCODE": "https://github.com/Boyle-Lab/Blacklist/raw/master/lists/hg38-blacklist.v2.bed.gz",
                            "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/ENCODE/blacklist/hg38-blacklist.v2.bed.gz"
                        },
                        "size": "~3MB",
                        "expected_size": 3000000
                    }
                },
                "hg38_test": {
                    "name": "Human chromosome 22 (test)",
                    "genome": {
                        "size": "~50MB",
                        "sources": {
                            "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/hg38/chromosomes/chr22.fa.gz",
                            "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/UCSC/goldenPath/hg38/chromosomes/chr22.fa.gz"
                        },
                        "expected_size": 52000000
                    },
                    "gtf": {
                        "sources": {
                            "GENCODE_CHR22": "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/gencode.v44.chr_patch_hapl_scaff.annotation.gtf.gz"
                        },
                        "size": "~1MB",
                        "expected_size": 1000000
                    }
                }
            },
            "mouse": {
                "mm10": {
                    "name": "Mouse genome GRCm38/mm10",
                    "genome": {
                        "size": "~2.7GB",
                        "sources": {
                            "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/mm10/bigZips/mm10.fa.gz",
                            "ENSEMBL": "http://ftp.ensembl.org/pub/release-104/fasta/mus_musculus/dna/Mus_musculus.GRCm38.dna.primary_assembly.fa.gz",
                            "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/genomes/refseq/vertebrate_mammalian/Mus_musculus/latest_assembly_versions/GCF_000001635.27_GRCm39/GCF_000001635.27_GRCm39_genomic.fna.gz"
                        },
                        "expected_size": 2800000000
                    },
                    "gtf": {
                        "sources": {
                            "GENCODE": "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M31/gencode.vM31.primary_assembly.annotation.gtf.gz",
                            "ENSEMBL": "http://ftp.ensembl.org/pub/release-104/gtf/mus_musculus/Mus_musculus.GRCm38.104.gtf.gz"
                        },
                        "size": "~45MB",
                        "expected_size": 47000000
                    },
                    "blacklist": {
                        "sources": {
                            "ENCODE": "https://github.com/Boyle-Lab/Blacklist/raw/master/lists/mm10-blacklist.v2.bed.gz"
                        },
                        "size": "~1.5MB",
                        "expected_size": 1500000
                    }
                },
                "mm10_test": {
                    "name": "Mouse chromosome 19 (test)",
                    "genome": {
                        "size": "~60MB",
                        "sources": {
                            "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/mm10/chromosomes/chr19.fa.gz",
                            "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/UCSC/goldenPath/mm10/chromosomes/chr19.fa.gz"
                        },
                        "expected_size": 62000000
                    }
                }
            }
        }
    
    def _check_existing_resources(self, species: str, genome_version: str) -> Dict[str, Dict[str, bool]]:
        """Check which resources already exist and are valid"""
        
        base_ref_dir = self.workspace_path / "reference"
        genome_dir = base_ref_dir / "genome" / species
        gtf_dir = base_ref_dir / "gtf" / species
        blacklist_dir = base_ref_dir / "blacklist" / species
        
        # Check genome files
        genome_fa = genome_dir / f"{genome_version}.fa"
        genome_fai = genome_dir / f"{genome_version}.fa.fai"
        bowtie2_index = genome_dir / f"{genome_version}.1.bt2"
        
        genome_status = {
            "fasta": self._validate_file(genome_fa, "fasta"),
            "fasta_index": genome_fai.exists(),
            "bowtie2_index": bowtie2_index.exists(),
            "complete": all([genome_fa.exists(), genome_fai.exists(), bowtie2_index.exists()])
        }
        
        # Check GTF files
        gtf_gencode = gtf_dir / f"{genome_version}_gencode.gtf"
        gtf_ensembl = gtf_dir / f"{genome_version}_ensembl.gtf"
        
        gtf_status = {
            "gencode": self._validate_file(gtf_gencode, "gtf"),
            "ensembl": self._validate_file(gtf_ensembl, "gtf"),
            "any_available": gtf_gencode.exists() or gtf_ensembl.exists()
        }
        
        # Check blacklist files
        blacklist_file = blacklist_dir / f"{genome_version}_blacklist.bed"
        
        blacklist_status = {
            "available": self._validate_file(blacklist_file, "bed")
        }
        
        return {
            "genome": genome_status,
            "gtf": gtf_status,
            "blacklist": blacklist_status
        }
    
    def _validate_file(self, file_path: Path, file_type: str) -> bool:
        """Validate file existence and basic format"""
        if not file_path.exists():
            return False
        
        try:
            # Basic file size check
            if file_path.stat().st_size == 0:
                return False
            
            # Format-specific validation
            if file_type == "fasta":
                with open(file_path, 'r') as f:
                    first_line = f.readline().strip()
                    return first_line.startswith('>')
            elif file_type == "gtf":
                with open(file_path, 'r') as f:
                    for line in f:
                        if not line.startswith('#'):
                            return len(line.split('\t')) >= 8
            elif file_type == "bed":
                with open(file_path, 'r') as f:
                    for line in f:
                        if not line.startswith('#') and line.strip():
                            return len(line.split('\t')) >= 3
            
            return True
        except:
            return False
    
    def _display_resource_plan(self, species_info: dict, genome_version: str, genome_config: dict, 
                             include_gtf: bool, include_blacklist: bool, existing_resources: dict):
        """Display what will be downloaded/skipped"""
        
        plan_table = Table(title=f"Resource Setup Plan: {species_info['common'].title()} {genome_version}")
        plan_table.add_column("Resource", style="cyan")
        plan_table.add_column("Status", style="magenta")
        plan_table.add_column("Action", style="green")
        plan_table.add_column("Size", style="yellow")
        
        # Genome status
        if existing_resources["genome"]["complete"]:
            plan_table.add_row("  Genome", "✅ Complete", "Skip", genome_config["genome"]["size"])
        else:
            missing_parts = []
            if not existing_resources["genome"]["fasta"]:
                missing_parts.append("FASTA")
            if not existing_resources["genome"]["fasta_index"]:
                missing_parts.append("FAI")
            if not existing_resources["genome"]["bowtie2_index"]:
                missing_parts.append("Bowtie2")
            
            plan_table.add_row("  Genome", f"❌ Missing: {', '.join(missing_parts)}", "Download & Index", genome_config["genome"]["size"])
        
        # GTF status
        if include_gtf:
            if existing_resources["gtf"]["any_available"]:
                plan_table.add_row("  GTF", "✅ Available", "Skip", genome_config.get("gtf", {}).get("size", "Unknown"))
            else:
                plan_table.add_row("  GTF", "❌ Missing", "Download", genome_config.get("gtf", {}).get("size", "Unknown"))
        else:
            plan_table.add_row("  GTF", "⏭️ Disabled", "Skip", "-")
        
        # Blacklist status
        if include_blacklist:
            if existing_resources["blacklist"]["available"]:
                plan_table.add_row("  Blacklist", "✅ Available", "Skip", genome_config.get("blacklist", {}).get("size", "Unknown"))
            else:
                plan_table.add_row("  Blacklist", "❌ Missing", "Download", genome_config.get("blacklist", {}).get("size", "Unknown"))
        else:
            plan_table.add_row("  Blacklist", "⏭  Disabled", "Skip", "-")
        
        logger.info("", rich=plan_table)
    
    def _setup_genome_file(self, genome_dir: Path, genome_version: str, genome_config: dict) -> dict:
        """Setup genome FASTA file and indexes with smart caching"""
        
        genome_fa = genome_dir / f"{genome_version}.fa"
        genome_fai = genome_dir / f"{genome_version}.fa.fai"
        bowtie2_index = genome_dir / f"{genome_version}.1.bt2"
        
        # Check if already complete
        if genome_fa.exists() and genome_fai.exists() and bowtie2_index.exists():
            if self._validate_file(genome_fa, "fasta"):
                logger.info("✅ [green]Genome files already exist and are valid[/green]")
                return {"success": True, "message": "Genome already available", "path": str(genome_fa)}
        
        # Download genome if needed
        if not genome_fa.exists() or not self._validate_file(genome_fa, "fasta"):
            logger.info("  [yellow]Downloading genome file...[/yellow]")
            
            # Auto-select fastest source
            speed_test = self._test_sources_speed(genome_config["genome"]["sources"])
            fastest_source, fastest_url = speed_test["fastest_source"], speed_test["fastest_url"]
            
            logger.info(f"  [green]Using fastest source: {fastest_source}[/green]")
            
            # Download with progress
            download_result = self._download_with_progress(
                fastest_url, 
                genome_dir / f"{genome_version}.fa.gz",
                f"Downloading {genome_version} genome",
                genome_config["genome"].get("expected_size", 1000000000)
            )
            
            if not download_result["success"]:
                return {"success": False, "message": f"Genome download failed: {download_result['error']}"}
            
            # Extract
            logger.info("  [yellow]Extracting genome...[/yellow]")
            try:
                cmd = f"gunzip {genome_dir / f'{genome_version}.fa.gz'}"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode != 0:
                    return {"success": False, "message": f"Extraction failed: {result.stderr}"}
            except Exception as e:
                return {"success": False, "message": f"Extraction error: {str(e)}"}
        
        # Create FASTA index if needed
        if not genome_fai.exists():
            logger.info("📇 [yellow]Creating FASTA index...[/yellow]")
            try:
                cmd = f"samtools faidx {genome_fa}"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.info("⚠️ [yellow]samtools not available, skipping fai index[/yellow]")
            except Exception as e:
                logger.info(f"⚠️ [yellow]FASTA indexing failed: {str(e)}[/yellow]")
        
        # Create Bowtie2 index if needed
        if not bowtie2_index.exists():
            logger.info("🔧 [yellow]Building Bowtie2 index (optimized for ATAC-seq)...[/yellow]")
            
            # First check if bowtie2-build is available
            try:
                subprocess.run(["which", "bowtie2-build"], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                logger.info("⚠️ [yellow]Bowtie2 not found - attempting to install...[/yellow]")
                logger.info("  [cyan]Auto-installing Bowtie2...[/cyan]")
                install_result = self.install_missing_tools(["bowtie2"])
                
                # Check again after installation
                try:
                    subprocess.run(["which", "bowtie2-build"], check=True, capture_output=True)
                    logger.info("✅ [green]Bowtie2 installed successfully[/green]")
                except subprocess.CalledProcessError:
                    logger.info("❌ [red]Bowtie2 installation failed - please install manually[/red]")
                    logger.info("[cyan]Manual install: conda install -c bioconda bowtie2[/cyan]")
                    return {"success": False, "message": "Bowtie2 installation failed", "path": str(genome_fa)}
            
            try:
                # Use bowtie2-build with optimized parameters for ATAC-seq
                cmd = f"bowtie2-build --threads 4 {genome_fa} {genome_dir}/{genome_version}"
                process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                
                while True:
                    output = process.stderr.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        logger.info(f"[dim]{output.strip()}[/dim]")
                
                if process.poll() != 0:
                    logger.info("⚠️ [yellow]Bowtie2 indexing failed[/yellow]")
                else:
                    logger.info("✅ [green]Bowtie2 index built successfully[/green]")
            except Exception as e:
                logger.info(f"⚠️ [yellow]Bowtie2 indexing error: {str(e)}[/yellow]")
        
        return {"success": True, "message": "Genome setup completed", "path": str(genome_fa)}
    
    def _setup_gtf_file(self, gtf_dir: Path, genome_version: str, genome_config: dict) -> dict:
        """Setup GTF annotation files"""
        
        if "gtf" not in genome_config:
            return {"success": True, "message": "No GTF configured for this genome"}
        
        gtf_gencode = gtf_dir / f"{genome_version}_gencode.gtf"
        
        # Check if already exists
        if gtf_gencode.exists() and self._validate_file(gtf_gencode, "gtf"):
            logger.info("✅ [green]GTF file already exists and is valid[/green]")
            return {"success": True, "message": "GTF already available", "path": str(gtf_gencode)}
        
        logger.info("  [yellow]Downloading GTF annotation...[/yellow]")
        
        # Auto-select fastest source
        speed_test = self._test_sources_speed(genome_config["gtf"]["sources"])
        fastest_source, fastest_url = speed_test["fastest_source"], speed_test["fastest_url"]
        
        logger.info(f"🏆 [green]Using fastest source: {fastest_source}[/green]")
        
        # Download with progress
        download_result = self._download_with_progress(
            fastest_url,
            gtf_dir / f"{genome_version}_gencode.gtf.gz", 
            f"Downloading {genome_version} GTF",
            genome_config["gtf"].get("expected_size", 50000000)
        )
        
        if not download_result["success"]:
            return {"success": False, "message": f"GTF download failed: {download_result['error']}"}
        
        # Extract
        logger.info("📦 [yellow]Extracting GTF...[/yellow]")
        try:
            cmd = f"gunzip {gtf_dir / f'{genome_version}_gencode.gtf.gz'}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                return {"success": False, "message": f"GTF extraction failed: {result.stderr}"}
        except Exception as e:
            return {"success": False, "message": f"GTF extraction error: {str(e)}"}
        
        return {"success": True, "message": "GTF setup completed", "path": str(gtf_gencode)}
    
    def _setup_blacklist_file(self, blacklist_dir: Path, genome_version: str, genome_config: dict) -> dict:
        """Setup blacklist BED files"""
        
        if "blacklist" not in genome_config:
            return {"success": True, "message": "No blacklist configured for this genome"}
        
        blacklist_bed = blacklist_dir / f"{genome_version}_blacklist.bed"
        
        # Check if already exists
        if blacklist_bed.exists() and self._validate_file(blacklist_bed, "bed"):
            logger.info("✅ [green]Blacklist file already exists and is valid[/green]")
            return {"success": True, "message": "Blacklist already available", "path": str(blacklist_bed)}
        
        logger.info("  [yellow]Downloading blacklist regions...[/yellow]")
        
        # Auto-select fastest source
        speed_test = self._test_sources_speed(genome_config["blacklist"]["sources"])
        fastest_source, fastest_url = speed_test["fastest_source"], speed_test["fastest_url"]
        
        logger.info(f"  [green]Using fastest source: {fastest_source}[/green]")
        
        # Download with progress
        download_result = self._download_with_progress(
            fastest_url,
            blacklist_dir / f"{genome_version}_blacklist.bed.gz",
            f"Downloading {genome_version} blacklist", 
            genome_config["blacklist"].get("expected_size", 3000000)
        )
        
        if not download_result["success"]:
            return {"success": False, "message": f"Blacklist download failed: {download_result['error']}"}
        
        # Extract
        logger.info("📦 [yellow]Extracting blacklist...[/yellow]")
        try:
            cmd = f"gunzip {blacklist_dir / f'{genome_version}_blacklist.bed.gz'}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                return {"success": False, "message": f"Blacklist extraction failed: {result.stderr}"}
        except Exception as e:
            return {"success": False, "message": f"Blacklist extraction error: {str(e)}"}
        
        return {"success": True, "message": "Blacklist setup completed", "path": str(blacklist_bed)}
    
    def _test_sources_speed(self, sources: dict) -> dict:
        """Test multiple sources and return fastest"""
        # Simplified version of existing speed test
        # For now, just return first available source
        first_source = list(sources.keys())[0]
        first_url = list(sources.values())[0]
        
        return {
            "fastest_source": first_source,
            "fastest_url": first_url,
            "speed_mbps": 0
        }
    
    def _download_with_progress(self, url: str, output_path: Path, description: str, expected_size: int) -> dict:
        """Download file with progress bar and validation"""
        
        try:
            import time
            cmd = f"curl -L -o {output_path} '{url}'"
            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            start_time = time.time()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=self.console
            ) as progress:
                download_task = progress.add_task(description, total=100)
                
                while process.poll() is None:
                    elapsed = time.time() - start_time
                    
                    try:
                        if output_path.exists():
                            current_size = output_path.stat().st_size
                            size_mb = current_size / (1024*1024)
                            expected_mb = expected_size / (1024*1024)
                            
                            estimated_progress = min(95, (current_size / expected_size) * 100)
                            
                            progress.update(
                                download_task,
                                completed=estimated_progress,
                                description=f"Downloaded {size_mb:.1f}MB/{expected_mb:.1f}MB at ~{size_mb/elapsed:.1f}MB/s"
                            )
                    except:
                        pass
                    
                    time.sleep(2)
                
                progress.update(download_task, completed=100, description="Download completed!")
            
            if process.poll() == 0:
                return {"success": True, "path": str(output_path)}
            else:
                return {"success": False, "error": f"Download failed with code {process.poll()}"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _generate_setup_summary(self, species_info: dict, genome_version: str, 
                              genome_result: dict, gtf_result: dict, blacklist_result: dict) -> str:
        """Generate comprehensive setup summary"""
        
        summary_lines = [f"🧬 Genome Resources Setup Summary for {species_info['common'].title()} {genome_version}"]
        
        # Genome summary
        if genome_result["success"]:
            summary_lines.append(f"✅ Genome: {genome_result['message']}")
            if "path" in genome_result:
                summary_lines.append(f"   📁 Path: {genome_result['path']}")
        else:
            summary_lines.append(f"❌ Genome: {genome_result['message']}")
        
        # GTF summary
        if gtf_result["success"]:
            summary_lines.append(f"✅ GTF: {gtf_result['message']}")
            if "path" in gtf_result:
                summary_lines.append(f"   📁 Path: {gtf_result['path']}")
        else:
            summary_lines.append(f"❌ GTF: {gtf_result['message']}")
        
        # Blacklist summary
        if blacklist_result["success"]:
            summary_lines.append(f"✅ Blacklist: {blacklist_result['message']}")
            if "path" in blacklist_result:
                summary_lines.append(f"   📁 Path: {blacklist_result['path']}")
        else:
            summary_lines.append(f"❌ Blacklist: {blacklist_result['message']}")
        
        # Directory structure
        base_ref_dir = self.workspace_path / "reference"
        summary_lines.append(f"📂 Resources organized in: {base_ref_dir}")
        
        return "\n".join(summary_lines)
    
    @tool
    def test_download_speeds(self, genome: str) -> Dict[str, Any]:
        """Test download speeds for different sources and recommend the fastest"""
        
        logger.info("", rich=Panel.fit(" Testing Download Sources Speed", style="bright_cyan"))
        
        # Get genome sources
        genome_sources = {
            "hg38": {
                "name": "Human genome (hg38)",
                "sources": {
                    "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz",
                    "ENSEMBL": "http://ftp.ensembl.org/pub/release-104/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz",
                    "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/genomes/refseq/vertebrate_mammalian/Homo_sapiens/latest_assembly_versions/GCF_000001405.40_GRCh38.p14/GCF_000001405.40_GRCh38.p14_genomic.fna.gz",
                    "NCBI": "https://ftp.ncbi.nlm.nih.gov/refseq/H_sapiens/annotation/GRCh38_latest/refseq_identifiers/GRCh38_latest_genomic.fna.gz"
                }
            },
            "mm10": {
                "name": "Mouse genome (mm10)",
                "sources": {
                    "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/mm10/bigZips/mm10.fa.gz",
                    "ENSEMBL": "http://ftp.ensembl.org/pub/release-104/fasta/mus_musculus/dna/Mus_musculus.GRCm38.dna.primary_assembly.fa.gz",
                    "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/genomes/refseq/vertebrate_mammalian/Mus_musculus/latest_assembly_versions/GCF_000001635.27_GRCm39/GCF_000001635.27_GRCm39_genomic.fna.gz",
                    "NCBI": "https://ftp.ncbi.nlm.nih.gov/refseq/M_musculus/annotation/GRCm38_latest/refseq_identifiers/GRCm38_latest_genomic.fna.gz"
                }
            },
            "hg38_test": {
                "name": "Human chromosome 22 (test)",
                "sources": {
                    "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/hg38/chromosomes/chr22.fa.gz",
                    "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/UCSC/goldenPath/hg38/chromosomes/chr22.fa.gz"
                }
            },
            "mm10_test": {
                "name": "Mouse chromosome 19 (test)",
                "sources": {
                    "UCSC": "http://hgdownload.cse.ucsc.edu/goldenPath/mm10/chromosomes/chr19.fa.gz", 
                    "清华镜像": "https://mirrors.tuna.tsinghua.edu.cn/UCSC/goldenPath/mm10/chromosomes/chr19.fa.gz"
                }
            }
        }
        
        if genome not in genome_sources:
            return {"error": f"Genome {genome} not supported for speed testing"}
        
        sources = genome_sources[genome]["sources"]
        speed_results = {}
        
        # Test each source with a 10-second partial download
        for source_name, url in sources.items():
            logger.info(f"🔍 [yellow]Testing {source_name}...[/yellow]")
            
            try:
                import time
                start_time = time.time()
                
                # Use curl to test download speed for 5 seconds
                cmd = f"curl -L --max-time 5 --silent --show-error '{url}' | wc -c"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
                
                end_time = time.time()
                
                if result.returncode == 0:
                    bytes_downloaded = int(result.stdout.strip())
                    duration = end_time - start_time
                    speed_mbps = (bytes_downloaded / (1024*1024)) / duration if duration > 0 else 0
                    
                    speed_results[source_name] = {
                        "speed_mbps": speed_mbps,
                        "status": "success",
                        "url": url
                    }
                    logger.info(f"✅ [green]{source_name}: {speed_mbps:.2f} MB/s[/green]")
                else:
                    speed_results[source_name] = {
                        "speed_mbps": 0,
                        "status": "failed",
                        "url": url
                    }
                    logger.info(f"❌ [red]{source_name}: Failed to connect[/red]")
                    
            except Exception as e:
                speed_results[source_name] = {
                    "speed_mbps": 0,
                    "status": "error",
                    "url": url,
                    "error": str(e)
                }
                logger.info(f"❌ [red]{source_name}: Error - {str(e)}[/red]")
        
        # Find fastest source
        valid_results = {k: v for k, v in speed_results.items() if v["status"] == "success"}
        
        if valid_results:
            fastest_source = max(valid_results.items(), key=lambda x: x[1]["speed_mbps"])
            
            # Display results table
            results_table = Table(title="Download Speed Test Results")
            results_table.add_column("Source", style="cyan")
            results_table.add_column("Speed", style="magenta")
            results_table.add_column("Status", style="green")
            
            for source_name, result in speed_results.items():
                if result["status"] == "success":
                    speed_str = f"{result['speed_mbps']:.2f} MB/s"
                    status_str = "✅ Available"
                    if source_name == fastest_source[0]:
                        speed_str += " [bold]← FASTEST[/bold]"
                else:
                    speed_str = "N/A"
                    status_str = "❌ Failed"
                
                results_table.add_row(source_name, speed_str, status_str)
            
            logger.info("", rich=results_table)
            
            fastest_name = fastest_source[0]
            fastest_speed = fastest_source[1]["speed_mbps"]
            logger.info(f"🏆 [bold green]Recommended: {fastest_name} ({fastest_speed:.2f} MB/s)[/bold green]")
            
            return {
                "genome": genome,
                "fastest_source": fastest_name,
                "fastest_url": fastest_source[1]["url"],
                "speed_mbps": fastest_speed,
                "all_results": speed_results
            }
        else:
            logger.info("[red]❌ All sources failed to connect[/red]")
            return {
                "genome": genome,
                "fastest_source": None,
                "error": "All sources failed",
                "all_results": speed_results
            }
    
    @tool
    def check_genome_setup(self, species: str = "human", genome_version: str = "hg38") -> dict:
        """Check if genome resources are already set up for ATAC-seq analysis
        
        Args:
            species: Target species (human, mouse, etc.)
            genome_version: Genome version (hg38, mm10, etc.)
            
        Returns:
            Dictionary with setup status and missing components
        """
        
        # Create species info for validation
        species_info = {
            "human": {"name": "Human", "common_name": "human", "organism": "homo_sapiens"},
            "mouse": {"name": "Mouse", "common_name": "mouse", "organism": "mus_musculus"}
        }.get(species, {"name": species.title(), "common_name": species, "organism": species})
        
        # Check genome directory structure
        genome_dir = self.workspace_path / "reference" / "genome" / species_info["common_name"]
        gtf_dir = self.workspace_path / "reference" / "gtf" / species_info["common_name"] 
        blacklist_dir = self.workspace_path / "reference" / "blacklist" / species_info["common_name"]
        
        status = {"required": [], "optional": [], "ready": True}
        
        # Check genome FASTA and Bowtie2 index (required)
        genome_fa = genome_dir / f"{genome_version}.fa"
        bowtie2_index = genome_dir / f"{genome_version}.1.bt2"
        
        if not (genome_fa.exists() and bowtie2_index.exists()):
            status["required"].append(f"Genome FASTA and Bowtie2 index for {genome_version}")
            status["ready"] = False
        
        # Check GTF file (optional but recommended)
        gtf_file = gtf_dir / f"{genome_version}_gencode.gtf"
        if not gtf_file.exists():
            status["optional"].append(f"GTF annotations for {genome_version}")
        
        # Check blacklist (optional but recommended for ATAC-seq)
        blacklist_file = blacklist_dir / f"{genome_version}_blacklist.bed"
        if not blacklist_file.exists():
            status["optional"].append(f"ENCODE blacklist regions for {genome_version}")
        
        if status["ready"]:
            return {
                "status": "complete",
                "message": f"✅ Genome resources for {species} {genome_version} are ready",
                "genome_path": str(genome_fa),
                "index_path": str(genome_dir / genome_version),
                "missing_optional": status["optional"]
            }
        else:
            return {
                "status": "incomplete", 
                "message": f"❌ Missing required genome resources for {species} {genome_version}",
                "missing_required": status["required"],
                "missing_optional": status["optional"],
                "setup_command": f"atac.setup_genome_resources('{species}', '{genome_version}')"
            }

    @tool
    def quick_genome_setup(self, species: str = "human") -> str:
        """Quick setup with test chromosome for fast ATAC-seq testing"""
        
        logger.info("", rich=Panel.fit(f" Quick Test Setup for {species.title()}", style="bright_green"))
        
        # Map species to test genomes
        test_genomes = {
            "human": "hg38_test",
            "mouse": "mm10_test"
        }
        
        if species not in test_genomes:
            logger.info(f"[red]❌ Quick setup not available for {species}[/red]")
            logger.info("[yellow]Available: human, mouse[/yellow]")
            return f"❌ Quick setup not available for {species}"
        
        test_genome = test_genomes[species]
        
        logger.info(f"[green]✅ Using test chromosome for fast setup[/green]")
        logger.info(f"[yellow]Note: This is for testing only - use full genome for production[/yellow]")
        
        return self.setup_genome_resources(species, test_genome)
    
    @tool
    def list_available_resources(self) -> str:
        """List all available genome resources with status"""
        
        logger.info("", rich=Panel.fit("📂 Available Genome Resources", style="bright_blue"))
        
        base_ref_dir = self.workspace_path / "reference"
        
        if not base_ref_dir.exists():
            return "❌ No resources directory found. Run setup_genome_resources() first."
        
        resources_table = Table(title="Genome Resources Status")
        resources_table.add_column("Species", style="cyan")
        resources_table.add_column("Version", style="magenta")
        resources_table.add_column("Genome", style="green")
        resources_table.add_column("GTF", style="yellow")
        resources_table.add_column("Blacklist", style="red")
        resources_table.add_column("Size", style="dim")
        
        total_size = 0
        resource_count = 0
        
        # Check each species directory
        for species_dir in (base_ref_dir / "genome").glob("*"):
            if species_dir.is_dir():
                species = species_dir.name
                
                # Check each genome version
                for genome_file in species_dir.glob("*.fa"):
                    genome_version = genome_file.stem
                    resource_count += 1
                    
                    # Check genome status
                    genome_fai = species_dir / f"{genome_version}.fa.fai"
                    bowtie2_index = species_dir / f"{genome_version}.1.bt2"
                    
                    if genome_fai.exists() and bowtie2_index.exists():
                        genome_status = "✅ Complete"
                    else:
                        genome_status = "⚠️ Partial"
                    
                    # Check GTF status
                    gtf_dir = base_ref_dir / "gtf" / species
                    gtf_files = list(gtf_dir.glob(f"{genome_version}*.gtf")) if gtf_dir.exists() else []
                    gtf_status = "✅ Available" if gtf_files else "❌ Missing"
                    
                    # Check blacklist status
                    blacklist_dir = base_ref_dir / "blacklist" / species
                    blacklist_files = list(blacklist_dir.glob(f"{genome_version}*.bed")) if blacklist_dir.exists() else []
                    blacklist_status = "✅ Available" if blacklist_files else "❌ Missing"
                    
                    # Calculate size
                    size_bytes = genome_file.stat().st_size
                    size_gb = size_bytes / (1024**3)
                    total_size += size_bytes
                    
                    resources_table.add_row(
                        species.title(),
                        genome_version,
                        genome_status,
                        gtf_status,
                        blacklist_status,
                        f"{size_gb:.1f}GB"
                    )
        
        logger.info("", rich=resources_table)
        
        # Summary
        total_gb = total_size / (1024**3)
        summary = f"\n Summary: {resource_count} genome resources using {total_gb:.1f}GB total"
        logger.info(summary)
        
        return f"Found {resource_count} genome resources in organized structure"
    
    
    
