"""
Pantheon-adapted worker for GeneAgent functionality
Replaces OpenAI dependency with Pantheon's built-in Agent capabilities
"""

import os
import time
import json
import re
from typing import Dict, List, Any, Callable

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

from .apis.get_complex_for_gene_set import get_complex_for_gene_set, get_complex_for_gene_set_doc 
from .apis.get_disease_for_single_gene import get_disease_for_single_gene, get_disease_for_single_gene_doc
from .apis.get_domain_for_single_gene import get_domain_for_single_gene, get_domain_for_single_gene_doc
from .apis.get_enrichment_for_gene_set import get_enrichment_for_gene_set, get_enrichment_for_gene_set_doc
from .apis.get_pathway_for_gene_set import get_pathway_for_gene_set, get_pathway_for_gene_set_doc  
from .apis.get_interactions_for_gene_set import get_interactions_for_gene_set, get_interactions_for_gene_set_doc 
from .apis.get_gene_summary_for_single_gene import get_gene_summary_for_single_gene, get_gene_summary_for_single_gene_doc
from .apis.get_pubmed_articles import get_pubmed_articles, get_pubmed_articles_doc

func2info = {
    "get_complex_for_gene_set": [get_complex_for_gene_set, get_complex_for_gene_set_doc],
    "get_disease_for_single_gene": [get_disease_for_single_gene, get_disease_for_single_gene_doc],
    "get_domain_for_single_gene": [get_domain_for_single_gene, get_domain_for_single_gene_doc],
    "get_enrichment_for_gene_set": [get_enrichment_for_gene_set, get_enrichment_for_gene_set_doc],
    "get_pathway_for_gene_set": [get_pathway_for_gene_set, get_pathway_for_gene_set_doc],
    "get_interactions_for_gene_set": [get_interactions_for_gene_set, get_interactions_for_gene_set_doc],
    "get_gene_summary_for_single_gene": [get_gene_summary_for_single_gene, get_gene_summary_for_single_gene_doc],
    "get_pubmed_articles": [get_pubmed_articles, get_pubmed_articles_doc]
}

pattern = re.compile(r'^[a-zA-Z0-9_\-\.\ ]+$')


class PantheonAgentPhD:
    """
    Pantheon-adapted version of AgentPhD that uses Pantheon's Agent capabilities
    instead of external OpenAI API
    """
    
    def __init__(self, function_names: List[str], agent_callback: Callable = None, show_progress: bool = True):
        """
        Initialize PantheonAgentPhD
        
        Args:
            function_names: List of function names to make available
            agent_callback: Callback function to Pantheon's Agent for LLM queries
            show_progress: Whether to show progress bars for operations
        """
        self.name2function = {function_name: func2info[function_name][0] for function_name in function_names}
        self.function_docs = [func2info[function_name][1] for function_name in function_names]
        self.agent_callback = agent_callback
        self.show_progress = show_progress
        self.console = Console()
        
        # Create function descriptions for the agent
        self.available_functions = {}
        for func_name in function_names:
            self.available_functions[func_name] = {
                "function": func2info[func_name][0],
                "doc": func2info[func_name][1]
            }
    
    def get_function_descriptions(self) -> str:
        """Get formatted function descriptions for the agent"""
        descriptions = []
        descriptions.append("Available functions for gene analysis:")
        
        for func_name, info in self.available_functions.items():
            doc = info["doc"]
            descriptions.append(f"\n- {func_name}: {doc}")
        
        return "\n".join(descriptions)
    
    def inference(self, claim: str) -> str:
        """
        Verify a claim using available functions and Pantheon's Agent
        
        Args:
            claim: The biological claim to verify
            
        Returns:
            Verification result with evidence
        """
        
        if self.show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=self.console
            ) as progress:
                # Setup task
                task = progress.add_task("🧬 Analyzing biological claim...", total=100)
                
                # Build the verification prompt
                progress.update(task, advance=20, description="📝 Building verification prompt...")
                time.sleep(0.1)  # Small delay for visual feedback
                
                system_prompt = """
You are a helpful fact-checker for biological claims. 
Your task is to verify the claim using the provided tools/functions.
If there are evidences in your research, please start your response with "Report:" and return your findings along with evidences.

Available functions:
"""
                
                function_descriptions = self.get_function_descriptions()
                
                content = f"""
Here is the claim that needs to be verified:
{claim}

Process:
1. Analyze the claim and determine which functions would be most helpful
2. Use the available functions to gather evidence
3. Provide a factual and objective verification
4. Start your final answer with "Report:" followed by your findings and evidence
5. Do not use any format symbols such as '*', '-' or other tokens

{function_descriptions}

Please proceed with the verification.
"""
                
                progress.update(task, advance=20, description="🔍 Extracting genes from claim...")
                time.sleep(0.1)
                
                # If we have an agent callback, use it
                if self.agent_callback:
                    try:
                        progress.update(task, advance=30, description="🤖 Processing with Agent...")
                        result = self.agent_callback(system_prompt + content)
                        progress.update(task, advance=20, description="📊 Processing results...")
                        final_result = self._process_agent_result(result, claim)
                        progress.update(task, advance=10, description="✅ Analysis complete!")
                        return final_result
                    except Exception as e:
                        progress.update(task, advance=50, description="❌ Agent error occurred")
                        return f"Report: Error during agent verification: {str(e)}"
                
                # Fallback: Use function calls directly
                progress.update(task, advance=30, description="🔬 Using direct function calls...")
                result = self._direct_function_verification_with_progress(claim, progress, task)
                progress.update(task, advance=10, description="✅ Verification complete!")
                return result
        else:
            # No progress bar version
            system_prompt = """
You are a helpful fact-checker for biological claims. 
Your task is to verify the claim using the provided tools/functions.
If there are evidences in your research, please start your response with "Report:" and return your findings along with evidences.

Available functions:
"""
            
            function_descriptions = self.get_function_descriptions()
            
            content = f"""
Here is the claim that needs to be verified:
{claim}

Process:
1. Analyze the claim and determine which functions would be most helpful
2. Use the available functions to gather evidence
3. Provide a factual and objective verification
4. Start your final answer with "Report:" followed by your findings and evidence
5. Do not use any format symbols such as '*', '-' or other tokens

{function_descriptions}

Please proceed with the verification.
"""
            
            # If we have an agent callback, use it
            if self.agent_callback:
                try:
                    result = self.agent_callback(system_prompt + content)
                    return self._process_agent_result(result, claim)
                except Exception as e:
                    return f"Report: Error during agent verification: {str(e)}"
            
            # Fallback: Use function calls directly
            return self._direct_function_verification(claim)
    
    def _process_agent_result(self, agent_result: str, claim: str) -> str:
        """Process the result from Pantheon's Agent"""
        
        # Extract function calls from agent result if any
        # This would need to be implemented based on how Pantheon's Agent
        # handles function calling
        
        # For now, return the agent's analysis
        if "Report:" not in agent_result:
            return f"Report: {agent_result}"
        
        return agent_result
    
    def _direct_function_verification_with_progress(self, claim: str, progress, task) -> str:
        """Direct function verification with progress tracking"""
        
        evidence_parts = []
        evidence_parts.append(f"Claim: {claim}")
        evidence_parts.append("\nDirect function verification:")
        
        # Extract gene names from claim
        progress.update(task, description="🧬 Extracting genes from claim...")
        time.sleep(0.1)
        genes = self._extract_genes_from_claim(claim)
        
        if genes:
            evidence_parts.append(f"\nIdentified genes: {', '.join(genes)}")
            
            # Calculate steps for progress
            total_steps = 0
            if "get_gene_summary_for_single_gene" in self.name2function:
                total_steps += min(len(genes), 3)
            if "get_pathway_for_gene_set" in self.name2function and len(genes) > 1:
                total_steps += 1
            if "get_enrichment_for_gene_set" in self.name2function and len(genes) > 1:
                total_steps += 1
            
            current_step = 0
            step_size = 20 / max(total_steps, 1)  # Remaining 20% divided by steps
            
            # Try relevant functions based on gene content
            try:
                # Gene summary
                if "get_gene_summary_for_single_gene" in self.name2function:
                    for gene in genes[:3]:  # Limit to first 3 genes
                        progress.update(task, description=f"📋 Getting summary for {gene}...")
                        time.sleep(0.05)
                        result = self.name2function["get_gene_summary_for_single_gene"](gene, "Homo")
                        evidence_parts.append(f"\nGene summary for {gene}: {str(result)[:200]}...")
                        current_step += 1
                        progress.update(task, advance=step_size)
                
                # Pathway information
                if "get_pathway_for_gene_set" in self.name2function and len(genes) > 1:
                    progress.update(task, description="🛤️ Analyzing pathways...")
                    time.sleep(0.1)
                    result = self.name2function["get_pathway_for_gene_set"](genes)
                    evidence_parts.append(f"\nPathway analysis: {str(result)[:200]}...")
                    current_step += 1
                    progress.update(task, advance=step_size)
                
                # Enrichment analysis
                if "get_enrichment_for_gene_set" in self.name2function and len(genes) > 1:
                    progress.update(task, description="📈 Running enrichment analysis...")
                    time.sleep(0.1)
                    result = self.name2function["get_enrichment_for_gene_set"](genes)
                    evidence_parts.append(f"\nEnrichment analysis: {str(result)[:200]}...")
                    current_step += 1
                    progress.update(task, advance=step_size)
                    
            except Exception as e:
                evidence_parts.append(f"\nFunction call error: {str(e)}")
                progress.update(task, description="❌ Error during function calls")
        
        else:
            evidence_parts.append("\nNo gene names clearly identified in claim.")
            progress.update(task, description="⚠️ No genes identified")
        
        evidence_parts.append(f"\nConclusion: Based on available data, this claim requires further investigation.")
        
        return "Report: " + "\n".join(evidence_parts)
    
    def _direct_function_verification(self, claim: str) -> str:
        """Fallback method using direct function calls (no progress bar)"""
        
        evidence_parts = []
        evidence_parts.append(f"Claim: {claim}")
        evidence_parts.append("\nDirect function verification:")
        
        # Extract gene names from claim
        genes = self._extract_genes_from_claim(claim)
        
        if genes:
            evidence_parts.append(f"\nIdentified genes: {', '.join(genes)}")
            
            # Try relevant functions based on gene content
            try:
                # Gene summary
                if "get_gene_summary_for_single_gene" in self.name2function:
                    for gene in genes[:3]:  # Limit to first 3 genes
                        result = self.name2function["get_gene_summary_for_single_gene"](gene, "Homo")
                        evidence_parts.append(f"\nGene summary for {gene}: {str(result)[:200]}...")
                
                # Pathway information
                if "get_pathway_for_gene_set" in self.name2function and len(genes) > 1:
                    result = self.name2function["get_pathway_for_gene_set"](genes)
                    evidence_parts.append(f"\nPathway analysis: {str(result)[:200]}...")
                
                # Enrichment analysis
                if "get_enrichment_for_gene_set" in self.name2function and len(genes) > 1:
                    result = self.name2function["get_enrichment_for_gene_set"](genes)
                    evidence_parts.append(f"\nEnrichment analysis: {str(result)[:200]}...")
                    
            except Exception as e:
                evidence_parts.append(f"\nFunction call error: {str(e)}")
        
        else:
            evidence_parts.append("\nNo gene names clearly identified in claim.")
        
        evidence_parts.append(f"\nConclusion: Based on available data, this claim requires further investigation.")
        
        return "Report: " + "\n".join(evidence_parts)
    
    def _extract_genes_from_claim(self, claim: str) -> List[str]:
        """Extract potential gene names from claim text"""
        
        # Simple heuristic: look for uppercase words that could be gene names
        words = claim.split()
        potential_genes = []
        
        for word in words:
            # Remove punctuation
            clean_word = re.sub(r'[^\w]', '', word)
            # Gene names are typically uppercase and 2-10 characters
            if clean_word.isupper() and 2 <= len(clean_word) <= 10:
                potential_genes.append(clean_word)
        
        return list(set(potential_genes))  # Remove duplicates


def create_agent_phd(function_names: List[str], agent_callback: Callable = None, show_progress: bool = True):
    """
    Factory function to create PantheonAgentPhD instance
    
    Args:
        function_names: List of function names to enable
        agent_callback: Callback to Pantheon's Agent
        show_progress: Whether to show progress bars
        
    Returns:
        PantheonAgentPhD instance
    """
    return PantheonAgentPhD(function_names, agent_callback, show_progress)


# Default function sets for common use cases
DEFAULT_FUNCTIONS = [
    "get_complex_for_gene_set",
    "get_disease_for_single_gene", 
    "get_domain_for_single_gene",
    "get_enrichment_for_gene_set",
    "get_pathway_for_gene_set",
    "get_interactions_for_gene_set",
    "get_gene_summary_for_single_gene",
    "get_pubmed_articles"
]

MINIMAL_FUNCTIONS = [
    "get_gene_summary_for_single_gene",
    "get_pathway_for_gene_set",
    "get_enrichment_for_gene_set"
]

PATHWAY_FUNCTIONS = [
    "get_pathway_for_gene_set",
    "get_enrichment_for_gene_set",
    "get_complex_for_gene_set"
]

CLINICAL_FUNCTIONS = [
    "get_disease_for_single_gene",
    "get_pubmed_articles",
    "get_gene_summary_for_single_gene"
]