"""API Call Monitor for GeneAgent - Visualize and track biological database calls"""

import time
import json
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.text import Text

class GeneAgentAPIMonitor:
    """Monitor and visualize GeneAgent API calls"""
    
    def __init__(self, workspace_path: Path = None):
        self.workspace_path = workspace_path or Path.cwd()
        self.console = Console()
        self.api_calls: List[Dict[str, Any]] = []
        self.api_stats = {
            "ncbi_eutils": {"calls": 0, "success": 0, "errors": 0},
            "gprofiler": {"calls": 0, "success": 0, "errors": 0},
            "enrichr": {"calls": 0, "success": 0, "errors": 0},
            "pubmed": {"calls": 0, "success": 0, "errors": 0},
            "other": {"calls": 0, "success": 0, "errors": 0}
        }
    
    def log_api_call(self, api_name: str, endpoint: str, params: Dict, response_time: float, success: bool, result: Any = None, error: str = None):
        """Log an API call for monitoring"""
        
        call_data = {
            "timestamp": datetime.now().isoformat(),
            "api_name": api_name,
            "endpoint": endpoint,
            "params": params,
            "response_time_ms": round(response_time * 1000, 2),
            "success": success,
            "result_size": len(str(result)) if result else 0,
            "error": error
        }
        
        self.api_calls.append(call_data)
        
        # Update stats
        api_category = self._categorize_api(api_name)
        self.api_stats[api_category]["calls"] += 1
        if success:
            self.api_stats[api_category]["success"] += 1
        else:
            self.api_stats[api_category]["errors"] += 1
    
    def _categorize_api(self, api_name: str) -> str:
        """Categorize API by service"""
        if "gene_summary" in api_name or "ncbi" in api_name.lower():
            return "ncbi_eutils"
        elif "enrichment" in api_name or "gprofiler" in api_name.lower():
            return "gprofiler"
        elif "pathway" in api_name or "enrichr" in api_name.lower():
            return "enrichr"
        elif "pubmed" in api_name:
            return "pubmed"
        else:
            return "other"
    
    def display_real_time_monitor(self):
        """Display real-time API call monitoring"""
        
        self.console.print("\n[bold cyan]🔬 GeneAgent API Monitor - Real-time Dashboard[/bold cyan]\n")
        
        # API Statistics Table
        table = Table(title="Biological Database API Calls", show_header=True, header_style="bold magenta")
        table.add_column("Service", style="cyan")
        table.add_column("Endpoint", style="green")
        table.add_column("Total Calls", justify="center")
        table.add_column("Success", justify="center", style="green")
        table.add_column("Errors", justify="center", style="red")
        table.add_column("Success Rate", justify="center")
        
        for service, stats in self.api_stats.items():
            if stats["calls"] > 0:
                success_rate = f"{(stats['success'] / stats['calls'] * 100):.1f}%" if stats["calls"] > 0 else "0%"
                
                # Map service to actual endpoints
                endpoint_map = {
                    "ncbi_eutils": "eutils.ncbi.nlm.nih.gov",
                    "gprofiler": "biit.cs.ut.ee/gprofiler",
                    "enrichr": "maayanlab.cloud/Enrichr",
                    "pubmed": "pubmed.ncbi.nlm.nih.gov",
                    "other": "various"
                }
                
                table.add_row(
                    service.upper().replace("_", " "),
                    endpoint_map.get(service, "unknown"),
                    str(stats["calls"]),
                    str(stats["success"]),
                    str(stats["errors"]),
                    success_rate
                )
        
        self.console.print(table)
        
        # Recent API Calls
        if self.api_calls:
            self.console.print("\n[bold yellow]📋 Recent API Calls (Last 5):[/bold yellow]")
            
            for call in self.api_calls[-5:]:
                status_icon = "✅" if call["success"] else "❌"
                time_str = call["timestamp"].split("T")[1][:8]
                
                call_info = Text()
                call_info.append(f"{status_icon} ", style="bold")
                call_info.append(f"[{time_str}] ", style="dim")
                call_info.append(f"{call['api_name']} ", style="cyan")
                call_info.append(f"({call['response_time_ms']}ms)", style="yellow")
                
                if not call["success"] and call["error"]:
                    call_info.append(f" - {call['error']}", style="red")
                
                self.console.print(call_info)
    
    def visualize_verification_workflow(self, genes: str):
        """Visualize the 7-step GeneAgent verification workflow with API calls"""
        
        self.console.print(f"\n[bold cyan]🧬 GeneAgent Verification Workflow for: {genes}[/bold cyan]\n")
        
        workflow_steps = [
            ("📝 Step 1", "Generate Baseline Analysis", "LLM", "No API calls"),
            ("🎯 Step 2", "Generate Topic Claims", "LLM", "No API calls"),
            ("🔍 Step 3", "Verify Topic Claims", "API", "8 biological APIs called"),
            ("✏️ Step 4", "Modify Analysis", "LLM", "No API calls"),
            ("🧬 Step 5", "Generate Analysis Claims", "LLM", "No API calls"),
            ("🔬 Step 6", "Verify Analysis Claims", "API", "8 biological APIs called"),
            ("📋 Step 7", "Final Summarization", "LLM", "No API calls")
        ]
        
        # Create workflow visualization
        workflow_table = Table(show_header=True, header_style="bold magenta", title="GeneAgent 7-Step Verification Process")
        workflow_table.add_column("Step", style="cyan", width=12)
        workflow_table.add_column("Description", style="white", width=25)
        workflow_table.add_column("Type", style="yellow", width=8)
        workflow_table.add_column("API Usage", style="green", width=30)
        
        for step, desc, type_col, api_usage in workflow_steps:
            workflow_table.add_row(step, desc, type_col, api_usage)
        
        self.console.print(workflow_table)
        
        # API Details Panel
        api_details = """
🔬 [bold]Biological APIs Used in Steps 3 & 6:[/bold]

[cyan]1. NCBI E-utilities[/cyan] - Gene summaries and functional information
   └ [dim]https://eutils.ncbi.nlm.nih.gov/entrez/eutils/[/dim]

[cyan]2. g:Profiler[/cyan] - GO enrichment analysis
   └ [dim]https://biit.cs.ut.ee/gprofiler/api/gost/profile/[/dim]

[cyan]3. Enrichr[/cyan] - Pathway analysis (KEGG, Reactome, MSigDB)
   └ [dim]http://maayanlab.cloud/Enrichr/[/dim]

[cyan]4. Protein Complexes[/cyan] - Protein complex membership data

[cyan]5. Disease Associations[/cyan] - Gene-disease relationship data

[cyan]6. Protein Domains[/cyan] - Structural domain information

[cyan]7. Protein Interactions[/cyan] - Protein-protein interaction networks

[cyan]8. PubMed Search[/cyan] - Literature evidence retrieval
        """
        
        panel = Panel(api_details, title="[bold]API Integration Details[/bold]", border_style="blue")
        self.console.print(panel)
    
    def show_api_comparison(self):
        """Show comparison: Current vs Required API implementation"""
        
        comparison_table = Table(title="API Implementation Status", show_header=True, header_style="bold magenta")
        comparison_table.add_column("API Function", style="cyan", width=30)
        comparison_table.add_column("Original GeneAgent", style="green", width=20)
        comparison_table.add_column("Pantheon Toolset", style="yellow", width=20)
        comparison_table.add_column("Status", style="white", width=15)
        
        apis = [
            ("get_gene_summary_for_single_gene", "✅ NCBI E-utils", "❌ Missing", "Need Implementation"),
            ("get_enrichment_for_gene_set", "✅ g:Profiler", "❌ Missing", "Need Implementation"),
            ("get_pathway_for_gene_set", "✅ Enrichr", "❌ Missing", "Need Implementation"),
            ("get_complex_for_gene_set", "✅ Complex DB", "❌ Missing", "Need Implementation"),
            ("get_disease_for_single_gene", "✅ Disease DB", "❌ Missing", "Need Implementation"),
            ("get_domain_for_single_gene", "✅ Domain DB", "❌ Missing", "Need Implementation"),
            ("get_interactions_for_gene_set", "✅ Interaction DB", "❌ Missing", "Need Implementation"),
            ("get_pubmed_articles", "✅ PubMed", "❌ Missing", "Need Implementation")
        ]
        
        for api_func, original, pantheon, status in apis:
            comparison_table.add_row(api_func, original, pantheon, status)
        
        self.console.print(comparison_table)
        
        self.console.print("\n[bold red]⚠️ Current Status:[/bold red]")
        self.console.print("[yellow]• The pantheon-toolset version is currently using simulated responses[/yellow]")
        self.console.print("[yellow]• Real API calls require implementing gene_agent_deps.py[/yellow]")
        self.console.print("[yellow]• Original 8 biological APIs are available and working[/yellow]")
    
    def save_api_logs(self):
        """Save API call logs to file"""
        log_file = self.workspace_path / "geneagent_api_logs.json"
        
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "stats": self.api_stats,
            "calls": self.api_calls
        }
        
        with open(log_file, 'w') as f:
            json.dump(log_data, f, indent=2)
        
        self.console.print(f"\n[green]📄 API logs saved to: {log_file}[/green]")


# Usage example
if __name__ == "__main__":
    monitor = GeneAgentAPIMonitor()
    
    # Show current implementation status
    monitor.show_api_comparison()
    
    # Visualize the workflow
    monitor.visualize_verification_workflow("TP53,BRCA1,EGFR")
    
    # Simulate some API calls for demo
    monitor.log_api_call("get_gene_summary", "ncbi_eutils", {"gene": "TP53"}, 0.45, True, {"summary": "tumor suppressor"})
    monitor.log_api_call("get_enrichment", "gprofiler", {"genes": ["TP53", "BRCA1"]}, 1.2, True, {"terms": ["DNA repair"]})
    monitor.log_api_call("get_pathway", "enrichr", {"genes": ["TP53", "BRCA1", "EGFR"]}, 0.8, False, error="Rate limit exceeded")
    
    # Display monitoring dashboard
    monitor.display_real_time_monitor()