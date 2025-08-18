"""
Agentic RAG Toolset for Bioinformatics Analysis
Enhances basic RAG with LLM-powered query understanding and code generation
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.utils.toolset import ToolSet, tool
from pantheon.toolsets.vector_rag import VectorRAGToolSet
from pantheon.utils.llm import completion


class AgenticRAGToolSet(ToolSet):
    """
    Enhanced RAG toolset that leverages LLM capabilities for:
    - Smart query expansion and rewriting
    - Context-aware retrieval
    - Code generation from documentation
    - Bioinformatics workflow automation
    """

    def __init__(
        self,
        name: str,
        db_path: str = None,  # Optional - can work without KB
        llm_model: str = "gpt-4",
        enable_code_execution: bool = True,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.db_path = Path(db_path) if db_path else None
        self.llm_model = llm_model
        self.enable_code_execution = enable_code_execution

        # Initialize base RAG if db_path provided
        if self.db_path and self.db_path.exists():
            self.vector_rag = VectorRAGToolSet(
                name=f"{name}_vector", db_path=str(self.db_path)
            )
        else:
            self.vector_rag = None
            print(f"Note: Running without knowledge base. Using LLM knowledge only.")

        # Initialize Python toolset if execution enabled
        if self.enable_code_execution:
            self.python_toolset = PythonInterpreterToolSet(name=f"{name}_python")

    @tool
    async def smart_bio_query(
        self, query: str, top_k: int = 5, include_examples: bool = True
    ) -> Dict[str, Any]:
        """
        Enhanced query that understands bioinformatics context and retrieves relevant docs

        Args:
            query: User's bioinformatics question or task
            top_k: Number of documents to retrieve
            include_examples: Whether to specifically search for code examples

        Returns:
            Dictionary with answer, sources, and relevant code examples
        """
        # Step 1: Expand query using LLM
        expansion_prompt = f"""
        Expand this bioinformatics query for better documentation search.
        Original query: "{query}"
        
        Add relevant:
        - Technical terms and synonyms
        - Common package names (scanpy, seurat, etc.)
        - Method names
        - Keep it under 50 words
        
        Expanded query:
        """

        expanded_query = await completion(
            model=self.llm_model,
            messages=[{"role": "user", "content": expansion_prompt}],
        )

        # Step 2: Multi-query retrieval (if KB available)
        all_results = []

        if self.vector_rag:
            queries = [
                query,  # Original
                expanded_query,  # Expanded
            ]

            if include_examples:
                queries.append(f"{query} example code tutorial")

            # Retrieve documents for each query
            seen_texts = set()

            for q in queries:
                results = await self.vector_rag.query_vector_db(
                    query=q, top_k=top_k // len(queries) + 1
                )

                # Deduplicate
                for r in results:
                    text_snippet = r["text"][:200]  # Use first 200 chars as key
                    if text_snippet not in seen_texts:
                        seen_texts.add(text_snippet)
                        all_results.append(r)
        else:
            # No KB - use pure LLM knowledge
            all_results = []

        # Step 3: Synthesize answer using LLM
        if all_results:
            context = "\n\n".join(
                [
                    f"Source: {r.get('metadata', {}).get('source', 'unknown')}\n{r['text']}"
                    for r in all_results[:top_k]
                ]
            )

            synthesis_prompt = f"""
            Question: {query}
            
            Based on the following documentation:
            {context}
            
            Provide a comprehensive answer that includes:
            1. Direct answer to the question
            2. Relevant methods and parameters
            3. Code example if applicable
            4. Common pitfalls to avoid
            
            Answer:
            """
        else:
            # No documentation available - use LLM knowledge directly
            synthesis_prompt = f"""
            Question: {query}
            
            As a bioinformatics expert, provide a comprehensive answer that includes:
            1. Direct answer to the question
            2. Relevant methods and parameters from common tools (scanpy, seurat, etc.)
            3. Code example if applicable
            4. Common pitfalls to avoid
            
            Base your answer on best practices in single-cell analysis.
            
            Answer:
            """

        synthesized_answer = await completion(
            model=self.llm_model,
            messages=[{"role": "user", "content": synthesis_prompt}],
        )

        return {
            "answer": synthesized_answer,
            "sources": all_results[:top_k],
            "expanded_query": expanded_query,
        }

    @tool
    async def generate_bio_code(
        self,
        task: str,
        data_path: Optional[str] = None,
        context: Optional[str] = None,
        execute: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate bioinformatics analysis code from natural language description

        Args:
            task: Description of the analysis task
            data_path: Path to data file if applicable
            context: Additional context or requirements
            execute: Whether to execute the generated code

        Returns:
            Dictionary with generated code and optional execution results
        """
        # Step 1: Understand the task
        intent_prompt = f"""
        Analyze this bioinformatics task:
        Task: {task}
        Data path: {data_path if data_path else "Not specified"}
        Context: {context if context else "None"}
        
        Identify:
        1. Analysis type (QC/clustering/DE/trajectory/visualization/etc)
        2. Required packages
        3. Key parameters to consider
        
        Return as JSON:
        """

        intent_response = await completion(
            model=self.llm_model, messages=[{"role": "user", "content": intent_prompt}]
        )

        try:
            # Extract JSON from response
            import re

            json_match = re.search(r"\{.*\}", intent_response, re.DOTALL)
            if json_match:
                intent = json.loads(json_match.group())
            else:
                intent = {
                    "analysis_type": "general",
                    "packages": ["scanpy"],
                    "parameters": {},
                }
        except:
            intent = {
                "analysis_type": "general",
                "packages": ["scanpy"],
                "parameters": {},
            }

        # Step 2: Retrieve relevant documentation
        doc_results = await self.smart_bio_query(
            f"{task} {intent.get('analysis_type', '')} code example",
            top_k=3,
            include_examples=True,
        )

        # Step 3: Generate code
        code_prompt = f"""
        Generate complete Python code for this bioinformatics task:
        
        Task: {task}
        Data path: {data_path if data_path else "Use placeholder path"}
        Analysis type: {intent.get("analysis_type", "general")}
        
        Relevant documentation and examples:
        {doc_results["answer"]}
        
        Requirements:
        1. Include all necessary imports
        2. Add error handling for common issues
        3. Include informative print statements
        4. Generate visualizations where appropriate
        5. Add comments explaining each step
        6. Use best practices from the documentation
        
        Generate only the Python code, no explanations:
        """

        generated_code = await completion(
            model=self.llm_model, messages=[{"role": "user", "content": code_prompt}]
        )

        # Clean up code (remove markdown if present)
        if "```python" in generated_code:
            generated_code = generated_code.split("```python")[1].split("```")[0]
        elif "```" in generated_code:
            generated_code = generated_code.split("```")[1].split("```")[0]

        result = {
            "code": generated_code.strip(),
            "intent": intent,
            "documentation_used": [
                s.get("metadata", {}).get("source", "unknown")
                for s in doc_results["sources"]
            ],
        }

        # Step 4: Execute if requested
        if execute and self.enable_code_execution:
            try:
                execution_result = await self.python_toolset.run_code(generated_code)
                result["execution"] = execution_result
            except Exception as e:
                result["execution_error"] = str(e)

        return result

    @tool
    async def analyze_bio_data(
        self,
        query: str,
        data_path: str,
        auto_execute: bool = True,
        iterative: bool = False,
    ) -> Dict[str, Any]:
        """
        Complete bioinformatics analysis from natural language query

        Args:
            query: Natural language description of analysis
            data_path: Path to data file
            auto_execute: Whether to automatically execute generated code
            iterative: Whether to refine code based on errors

        Returns:
            Complete analysis results including code and outputs
        """
        # Generate initial code
        result = await self.generate_bio_code(
            task=query, data_path=data_path, execute=auto_execute
        )

        # If execution failed and iterative mode is on, try to fix
        if iterative and auto_execute and "execution_error" in result:
            error = result["execution_error"]

            # Get debugging help from RAG
            debug_query = await self.smart_bio_query(
                f"Error in bioinformatics code: {error}. How to fix?", top_k=3
            )

            # Regenerate code with error context
            fix_prompt = f"""
            The following code produced an error:
            
            Code:
            {result["code"]}
            
            Error:
            {error}
            
            Debugging suggestions:
            {debug_query["answer"]}
            
            Generate a fixed version of the code:
            """

            fixed_code = await completion(
                model=self.llm_model, messages=[{"role": "user", "content": fix_prompt}]
            )

            if "```python" in fixed_code:
                fixed_code = fixed_code.split("```python")[1].split("```")[0]
            elif "```" in fixed_code:
                fixed_code = fixed_code.split("```")[1].split("```")[0]

            # Try executing fixed code
            try:
                execution_result = await self.python_toolset.run_code(
                    fixed_code.strip()
                )
                result["code"] = fixed_code.strip()
                result["execution"] = execution_result
                result["fixed"] = True
                if "execution_error" in result:
                    del result["execution_error"]
            except Exception as e:
                result["second_error"] = str(e)

        return result

    @tool
    async def explain_bio_method(
        self,
        method: str,
        include_parameters: bool = True,
        include_examples: bool = True,
    ) -> Dict[str, Any]:
        """
        Explain a bioinformatics method with parameters and examples

        Args:
            method: Name of the method or function
            include_parameters: Include parameter explanations
            include_examples: Include code examples

        Returns:
            Detailed explanation with examples
        """
        # Query for method documentation
        query = f"{method} bioinformatics method"
        if include_parameters:
            query += " parameters options"
        if include_examples:
            query += " example usage"

        docs = await self.smart_bio_query(query, top_k=5)

        # Generate comprehensive explanation
        explain_prompt = f"""
        Explain the bioinformatics method: {method}
        
        Based on documentation:
        {docs["answer"]}
        
        Include:
        1. What the method does
        2. When to use it
        3. Key parameters and their typical values
        4. Simple code example
        5. Common issues and solutions
        
        Explanation:
        """

        explanation = await completion(
            model=self.llm_model, messages=[{"role": "user", "content": explain_prompt}]
        )

        return {
            "method": method,
            "explanation": explanation,
            "sources": docs["sources"],
        }

    @tool
    async def troubleshoot_bio_issue(
        self,
        issue: str,
        error_message: Optional[str] = None,
        code_snippet: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Troubleshoot bioinformatics analysis issues

        Args:
            issue: Description of the problem
            error_message: Specific error message if available
            code_snippet: Relevant code causing the issue

        Returns:
            Troubleshooting suggestions and solutions
        """
        # Build comprehensive query
        query_parts = [issue]
        if error_message:
            query_parts.append(f"error: {error_message}")

        # Search for solutions
        solutions = await self.smart_bio_query(
            " ".join(query_parts) + " solution fix troubleshoot", top_k=5
        )

        # Generate troubleshooting guide
        troubleshoot_prompt = f"""
        Troubleshoot this bioinformatics issue:
        Issue: {issue}
        Error: {error_message if error_message else "No specific error"}
        Code: {code_snippet if code_snippet else "No code provided"}
        
        Documentation and solutions found:
        {solutions["answer"]}
        
        Provide:
        1. Likely cause of the issue
        2. Step-by-step solution
        3. Code fix if applicable
        4. How to prevent this in the future
        
        Troubleshooting guide:
        """

        guide = await completion(
            model=self.llm_model,
            messages=[{"role": "user", "content": troubleshoot_prompt}],
        )

        return {
            "issue": issue,
            "troubleshooting_guide": guide,
            "sources": solutions["sources"],
        }
