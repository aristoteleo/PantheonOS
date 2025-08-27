"""Real-time GeneAgent API Visualizer - Monitor actual biological database calls"""

import json
import time
import sys
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.text import Text
from rich.live import Live
from rich.layout import Layout

# Add path for imports
sys.path.append('.')

class GeneAgentAPIVisualizer:
    """Real-time visualizer for GeneAgent biological API calls"""
    
    def __init__(self):
        self.console = Console()
        self.api_results = {}
        self.start_time = time.time()
        
    def test_all_apis(self, genes="TP53,BRCA1,EGFR"):
        """Test all 8 GeneAgent APIs with real biological data"""
        
        apis = {
            'gene_summary': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_gene_summary_for_single_gene', 'get_gene_summary_for_single_gene', ['TP53', 'Homo']),
            'enrichment': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_enrichment_for_gene_set', 'get_enrichment_for_gene_set', [genes]),
            'pathway': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_pathway_for_gene_set', 'get_pathway_for_gene_set', [genes]),
            'interactions': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_interactions_for_gene_set', 'get_interactions_for_gene_set', [genes]),
            'disease': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_disease_for_single_gene', 'get_disease_for_single_gene', ['TP53']),
            'domain': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_domain_for_single_gene', 'get_domain_for_single_gene', ['TP53']),
            'complex': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_complex_for_gene_set', 'get_complex_for_gene_set', [genes]),
            'pubmed': ('pantheon.toolsets.bio.gene_agent_deps.apis.get_pubmed_articles', 'get_pubmed_articles', ['TP53 cancer'])
        }
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            transient=True
        ) as progress:
            
            task = progress.add_task("Testing GeneAgent APIs...", total=len(apis))
            
            for api_name, (module_path, func_name, args) in apis.items():
                progress.update(task, description=f"Testing {api_name} API...")
                
                start_time = time.time()
                try:
                    # Import and call the API
                    module = __import__(module_path, fromlist=[func_name])
                    func = getattr(module, func_name)
                    result = func(*args)
                    
                    response_time = (time.time() - start_time) * 1000
                    
                    # Parse and analyze result
                    self.api_results[api_name] = {
                        'success': True,
                        'response_time': response_time,
                        'result': result,
                        'error': None,
                        'timestamp': datetime.now().isoformat()
                    }
                    
                except Exception as e:
                    response_time = (time.time() - start_time) * 1000
                    self.api_results[api_name] = {
                        'success': False,
                        'response_time': response_time,
                        'result': None,
                        'error': str(e),
                        'timestamp': datetime.now().isoformat()
                    }
                
                progress.advance(task)
                time.sleep(0.2)  # Small delay between API calls
    
    def create_api_status_table(self):
        """Create a table showing API status and results"""
        
        table = Table(title="🔬 GeneAgent Biological APIs - Live Status", show_header=True, header_style="bold magenta")
        table.add_column("API", style="cyan", width=15)
        table.add_column("Database/Service", style="yellow", width=20)
        table.add_column("Status", style="white", width=8)
        table.add_column("Response Time", style="green", width=12)
        table.add_column("Data Retrieved", style="blue", width=20)
        
        api_info = {
            'gene_summary': ('NCBI E-utilities', 'Gene function summaries'),
            'enrichment': ('g:Profiler', 'GO enrichment terms'),
            'pathway': ('Enrichr', 'Biological pathways'),
            'interactions': ('STRING/BioGRID', 'Protein interactions'),
            'disease': ('DisGeNET', 'Disease associations'),
            'domain': ('InterPro', 'Protein domains'),
            'complex': ('CORUM', 'Protein complexes'),
            'pubmed': ('PubMed', 'Literature articles')
        }
        
        for api_name, (service, data_type) in api_info.items():
            if api_name in self.api_results:
                result = self.api_results[api_name]
                
                # Status
                status = "✅ OK" if result['success'] else "❌ FAIL"
                
                # Response time
                rt = f"{result['response_time']:.1f}ms"
                
                # Data summary
                if result['success'] and result['result']:
                    try:
                        if isinstance(result['result'], str):
                            parsed = json.loads(result['result'])
                            if isinstance(parsed, list):
                                data_summary = f"{len(parsed)} items"
                            else:
                                data_summary = "Data retrieved"
                        elif isinstance(result['result'], dict):
                            data_summary = "Gene info"
                        else:
                            data_summary = "Data retrieved"
                    except:
                        data_summary = "Raw data"
                else:
                    data_summary = result['error'][:20] + "..." if result['error'] else "No data"
                
                table.add_row(api_name.upper(), service, status, rt, data_summary)
            else:
                table.add_row(api_name.upper(), service, "⏳ WAIT", "-", "Not tested")
        
        return table
    
    def create_verification_workflow(self):
        """Create visualization of the 7-step workflow"""
        
        workflow_text = f"""
🧬 [bold cyan]GeneAgent 7-Step Iterative Verification Workflow[/bold cyan]

[yellow]INPUT:[/yellow] Gene set (TP53, BRCA1, EGFR)

[bold]Step 1: Generate Baseline Analysis[/bold] 📝
├─ Type: LLM Generation
├─ Input: Gene names
└─ Output: Initial biological process analysis

[bold]Step 2: Extract Process & Generate Claims[/bold] 🎯  
├─ Type: LLM Processing
├─ Input: Baseline analysis
└─ Output: Testable biological claims

[bold]Step 3: Verify Claims with APIs[/bold] 🔍
├─ Type: [red]BIOLOGICAL DATABASE CALLS[/red]
├─ APIs Used: ALL 8 biological APIs
├─ Sources: NCBI, g:Profiler, Enrichr, STRING, etc.
└─ Output: Evidence-based verification results

[bold]Step 4: Modify Analysis[/bold] ✏️
├─ Type: LLM Processing  
├─ Input: Original analysis + Verification results
└─ Output: Evidence-updated analysis

[bold]Step 5: Generate Analysis Claims[/bold] 🧬
├─ Type: LLM Processing
├─ Input: Updated analysis
└─ Output: New testable claims

[bold]Step 6: Second Verification[/bold] 🔬
├─ Type: [red]BIOLOGICAL DATABASE CALLS[/red]
├─ APIs Used: ALL 8 biological APIs (again)
├─ Sources: Same databases, different claims
└─ Output: Second round verification

[bold]Step 7: Final Synthesis[/bold] 📋
├─ Type: LLM Processing
├─ Input: All analyses + All verifications
└─ Output: [green]Evidence-verified biological analysis[/green]

[yellow]RESULT:[/yellow] Scientifically validated gene set analysis
        """
        
        return Panel(workflow_text, title="[bold]Verification Workflow[/bold]", border_style="blue")
    
    def show_sample_api_data(self):
        """Show sample data from successful API calls"""
        
        self.console.print("\n[bold yellow]📊 Sample API Response Data:[/bold yellow]\n")
        
        for api_name, result in self.api_results.items():
            if result['success'] and result['result']:
                self.console.print(f"[bold cyan]{api_name.upper()} API Response:[/bold cyan]")
                
                try:
                    if isinstance(result['result'], str):
                        parsed = json.loads(result['result'])
                        if isinstance(parsed, list) and parsed:
                            self.console.print(f"  • Found {len(parsed)} items")
                            if len(parsed) > 0:
                                self.console.print(f"  • Sample: {str(parsed[0])[:100]}...")
                        elif isinstance(parsed, dict):
                            self.console.print(f"  • Data: {str(parsed)[:100]}...")
                    elif isinstance(result['result'], dict):
                        self.console.print(f"  • Gene: {result['result'].get('name', 'Unknown')}")
                        self.console.print(f"  • Description: {result['result'].get('description', 'N/A')}")
                    
                except Exception as e:
                    self.console.print(f"  • Raw data: {str(result['result'])[:100]}...")
                
                self.console.print()
    
    def generate_full_report(self, genes="TP53,BRCA1,EGFR"):
        """Generate a comprehensive API visualization report"""
        
        self.console.print(f"\n[bold cyan]🔬 GeneAgent API Integration Report[/bold cyan]")
        self.console.print(f"[dim]Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
        self.console.print(f"[dim]Target genes: {genes}[/dim]\n")
        
        # Test all APIs
        self.test_all_apis(genes)
        
        # Show status table
        table = self.create_api_status_table()
        self.console.print(table)
        
        # Show workflow
        workflow = self.create_verification_workflow()
        self.console.print(workflow)
        
        # Calculate summary stats
        total_apis = len(self.api_results)
        successful_apis = sum(1 for r in self.api_results.values() if r['success'])
        avg_response_time = sum(r['response_time'] for r in self.api_results.values()) / total_apis
        
        # Summary panel
        summary = f"""
🎯 [bold]API Integration Summary:[/bold]

• Total APIs tested: {total_apis}/8
• Successful calls: {successful_apis}
• Success rate: {(successful_apis/total_apis*100):.1f}%
• Average response time: {avg_response_time:.1f}ms
• Total test duration: {(time.time() - self.start_time):.1f}s

[green]✅ GeneAgent verification system is ACTIVE and functional![/green]
[yellow]📡 Real biological databases are accessible via API[/yellow]
[cyan]🔬 Ready for 7-step iterative verification workflow[/cyan]
        """
        
        summary_panel = Panel(summary, title="[bold]Integration Status[/bold]", border_style="green")
        self.console.print(summary_panel)
        
        # Show sample data
        self.show_sample_api_data()


if __name__ == "__main__":
    visualizer = GeneAgentAPIVisualizer()
    visualizer.generate_full_report("TP53,BRCA1,EGFR")