"""Bio Commands Handler for REPL"""

from rich.console import Console


class BioCommandHandler:
    """Handler for /bio commands in REPL"""
    
    def __init__(self, console: Console):
        self.console = console
    
    async def handle_bio_command(self, command: str) -> str:
        """
        Handle /bio commands for bioinformatics analysis
        
        Returns:
            str: Message to send to agent, or None if no message needed
        """
        # Parse command parts
        parts = command.split()
        
        if len(parts) == 1:
            # Just /bio - show help
            self._show_bio_help()
            return None
        
        # Route bio commands to the bio toolset
        if len(parts) >= 2:
            if parts[1] in ['list', 'info', 'help']:
                return self._handle_bio_manager_command(parts)
            else:
                return self._handle_tool_specific_command(parts)
        
        return None
    
    def _show_bio_help(self):
        """Show bio commands help"""
        self.console.print("\n[bold cyan]🧬 Bio Analysis Tools[/bold cyan]")
        self.console.print("[dim]/bio list[/dim] - List all available bio analysis tools")
        self.console.print("[dim]/bio info <tool>[/dim] - Get information about a specific tool")
        self.console.print("[dim]/bio help [tool][/dim] - Get help for bio tools")
        self.console.print("[dim]/bio <tool> <command>[/dim] - Run tool-specific commands")
        self.console.print("\n[dim]Examples:[/dim]")
        self.console.print("[dim]  /bio list                      # Show all available tools[/dim]")
        self.console.print("[dim]  /bio atac init                 # Initialize ATAC-seq project[/dim]")
        self.console.print("[dim]  /bio atac upstream ./data      # Run upstream ATAC analysis[/dim]")
        self.console.print("[dim]  /bio rnaseq init               # Initialize RNA-seq project (when available)[/dim]")
        self.console.print("")
    
    def _handle_bio_manager_command(self, parts) -> str:
        """Handle direct bio manager commands (list, info, help)"""
        method_name = parts[1]
        
        if len(parts) > 2 and parts[1] in ['info', 'help']:
            # Include tool name as parameter
            tool_name = parts[2]
            return f"bio {method_name} {tool_name}"
        else:
            return f"bio {method_name}"
    
    def _handle_tool_specific_command(self, parts) -> str:
        """Handle tool-specific commands"""
        tool_name = parts[1]
        
        # Handle ATAC commands with special logic
        if tool_name == "atac":
            return self._handle_atac_command(parts)
        
        # Generic handler for other tools
        if len(parts) > 2:
            method_name = parts[2]
            params = " ".join(parts[3:])  # Additional parameters
            if params:
                return f"bio_{tool_name}_{method_name} {params}"
            else:
                return f"bio_{tool_name}_{method_name}"
        else:
            # Just tool name, show tool help
            return f"bio info {tool_name}"
    
    def _handle_atac_command(self, parts) -> str:
        """Handle ATAC-specific commands with special logic like the original _handle_atac_command"""
        
        if len(parts) == 2:
            # Just /bio atac - show ATAC help
            self.console.print("\n[bold]🧬 ATAC-seq Analysis Helper[/bold]")
            self.console.print("[dim]/bio atac init[/dim] - Enter ATAC-seq analysis mode")
            self.console.print("[dim]/bio atac upstream <folder>[/dim] - Run upstream ATAC-seq analysis on folder")
            self.console.print("\n[dim]Examples:[/dim]")
            self.console.print("[dim]  /bio atac init                     # Enter ATAC mode[/dim]")
            self.console.print("[dim]  /bio atac upstream ./fastq_data   # Analyze FASTQ data[/dim]")
            self.console.print()
            return None
        
        command = parts[2]
        
        if command == "init":
            # Enter ATAC mode - simple mode activation without automation
            self.console.print("\n[bold cyan]🧬 Entering ATAC-seq Analysis Mode[/bold cyan]")
            
            # Clear all existing todos when entering ATAC mode
            clear_message = """
ATAC INIT MODE — STRICT

Goal: ONLY clear TodoList and report the new status. Do NOT create or execute anything.

Allowed tools (whitelist):
  - clear_all_todos()
  - show_todos()

Hard bans (do NOT call under any circumstance in init):
  - add_todo(), mark_task_done(), execute_current_task()
  - any atac.* analysis tools

Steps:
  1) clear_all_todos()
  2) todos = show_todos()

Response format (single line):
  ATAC init ready • todos={len(todos)}
"""
            
            self.console.print("[dim]Clearing existing todos and preparing ATAC environment...[/dim]")
            self.console.print("[dim]Ready for ATAC-seq analysis assistance...[/dim]")
            self.console.print("[dim]ATAC-seq mode activated. You can now use ATAC tools directly.[/dim]")
            self.console.print()
            self.console.print("[dim]The command structure is now clean:[/dim]")
            self.console.print("[dim]  - /bio atac init - Enter ATAC mode (simple prompt loading)[/dim]")
            self.console.print("[dim]  - /bio atac upstream <folder> - Run upstream analysis on specific folder[/dim]")
            self.console.print()
            
            return clear_message
        
        elif command == "upstream":
            # Handle upstream analysis
            if len(parts) < 4:
                self.console.print("[red]Error: Please specify a folder path[/red]")
                self.console.print("[dim]Usage: /bio atac upstream <folder_path>[/dim]")
                self.console.print("[dim]Example: /bio atac upstream ./fastq_data[/dim]")
                return None
                
            try:
                from ..cli.prompt.atac_bulk_upstream import generate_atac_analysis_message
                
                folder_path = parts[3]
                self.console.print(f"\n[bold cyan]🧬 Starting ATAC-seq Analysis[/bold cyan]")
                self.console.print(f"[dim]Target folder: {folder_path}[/dim]")
                self.console.print("[dim]Preparing analysis pipeline...[/dim]\n")
                
                # Generate the analysis message with folder
                atac_message = generate_atac_analysis_message(folder_path=folder_path)
                
                self.console.print("[dim]Sending ATAC-seq analysis request...[/dim]\n")
                
                return atac_message
                
            except ImportError as e:
                self.console.print(f"[red]Error: ATAC module not available: {e}[/red]")
                return None
            except Exception as e:
                self.console.print(f"[red]Error preparing analysis: {str(e)}[/red]")
                return None
        
        else:
            # Handle other ATAC commands generically
            params = " ".join(parts[3:]) if len(parts) > 3 else ""
            if params:
                return f"bio_atac_{command} {params}"
            else:
                return f"bio_atac_{command}"
    
    async def handle_deprecated_atac_command(self, command: str) -> str:
        """
        Handle deprecated /atac commands with migration and auto-conversion
        
        Returns:
            str: Converted bio command message, or None if no conversion
        """
        parts = command.split(maxsplit=2)
        
        # Show deprecation warning
        self.console.print("\n[bold yellow]⚠️  Command Migration Notice[/bold yellow]")
        self.console.print("[yellow]ATAC commands have moved to the unified bio interface![/yellow]")
        
        if len(parts) == 1:
            # Just /atac - show migration help
            self._show_atac_migration_help()
            return None
        
        # Auto-convert old commands to new bio commands
        if parts[1] == "init":
            self.console.print("\n[bold cyan]→ Auto-converting to: /bio atac init[/bold cyan]")
            return "bio_atac_init"
        
        elif parts[1] == "upstream":
            # Auto-convert upstream command
            if len(parts) < 3:
                self.console.print("[red]Error: Please specify a folder path[/red]")
                self.console.print("[dim]New usage: /bio atac upstream <folder_path>[/dim]")
                self.console.print("[dim]Example: /bio atac upstream ./fastq_data[/dim]")
                return None
            
            folder_path = parts[2]
            self.console.print(f"\n[bold cyan]→ Auto-converting to: /bio atac upstream {folder_path}[/bold cyan]")
            return f"bio_atac_upstream {folder_path}"
        
        else:
            self.console.print(f"[red]Unknown ATAC command: {parts[1]}[/red]")
            self.console.print("[dim]Please use the new bio interface instead:[/dim]")
            self.console.print("[dim]  /bio atac init - Initialize ATAC project[/dim]")
            self.console.print("[dim]  /bio atac upstream <folder> - Run upstream ATAC analysis[/dim]")
            return None
    
    def _show_atac_migration_help(self):
        """Show ATAC migration help"""
        self.console.print("\n[dim]Old commands → New commands:[/dim]")
        self.console.print("[dim]/atac init → /bio atac init[/dim]")
        self.console.print("[dim]/atac upstream <folder> → /bio atac upstream <folder>[/dim]")
        self.console.print("\n[bold cyan]🧬 Available Bio Commands[/bold cyan]")
        self.console.print("[dim]/bio list[/dim] - List all available bio tools")
        self.console.print("[dim]/bio atac init[/dim] - Initialize ATAC-seq project")
        self.console.print("[dim]/bio atac upstream <folder>[/dim] - Run upstream ATAC analysis")
        self.console.print("")


# Command mapping for easy extension
BIO_COMMAND_MAP = {
    # Direct bio manager commands
    'list': 'bio list',
    'help': 'bio help',
    'info': 'bio info',
    
    # ATAC-seq commands
    'atac_init': 'bio_atac_init',
    'atac_upstream': 'bio_atac_upstream',
    'atac_check_dependencies': 'bio_atac_check_dependencies',
    'atac_setup_genome_resources': 'bio_atac_setup_genome_resources',
    'atac_auto_align_fastq': 'bio_atac_auto_align_fastq',
    'atac_call_peaks_macs2': 'bio_atac_call_peaks_macs2',
    'atac_generate_atac_qc_report': 'bio_atac_generate_atac_qc_report',
    
    # RNA-seq commands (for future use)
    'rnaseq_init': 'bio_rnaseq_init',
    'rnaseq_align_reads': 'bio_rnaseq_align_reads',
    'rnaseq_diff_expression': 'bio_rnaseq_diff_expression',
    
    # ChIP-seq commands (for future use)
    'chipseq_init': 'bio_chipseq_init',
    'chipseq_call_peaks': 'bio_chipseq_call_peaks',
    'chipseq_find_motifs': 'bio_chipseq_find_motifs',
}

# Deprecated command conversions
DEPRECATED_ATAC_MAP = {
    '/atac init': '/bio atac init',
    '/atac upstream': '/bio atac upstream',
}

def get_bio_command_suggestions() -> list:
    """Get list of available bio command suggestions for autocomplete"""
    suggestions = [
        '/bio list',
        '/bio help',
        '/bio info atac',
        '/bio atac init',
        '/bio atac upstream',
        '/bio atac check_dependencies',
        '/bio atac setup_genome_resources',
        '/bio rnaseq init',  # Future
        '/bio chipseq init',  # Future
    ]
    return suggestions