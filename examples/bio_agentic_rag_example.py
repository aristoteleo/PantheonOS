#!/usr/bin/env python3
"""
Example usage of the Agentic RAG toolset for bioinformatics analysis
"""

import asyncio
from pathlib import Path
from pantheon.agent import Agent
from pantheon.toolsets.agentic_rag import AgenticRAGToolSet
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.shell import ShellToolSet


async def build_knowledge_base():
    """Build the bioinformatics knowledge base from configuration"""
    from pantheon.toolsets.utils.rag import build_all

    config_path = Path("configs/bio_knowledge.yaml")
    output_dir = Path("./rag_databases")

    if not output_dir.exists():
        print("Building bioinformatics knowledge base...")
        print("This may take a while on first run...")
        await build_all(str(config_path), str(output_dir))
        print("Knowledge base built successfully!")
    else:
        print("Knowledge base already exists. Skipping build.")


async def example_basic_query():
    """Example: Basic documentation query"""
    print("\n" + "=" * 60)
    print("Example 1: Basic Documentation Query")
    print("=" * 60)

    # Initialize the agentic RAG
    rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path="./rag_databases/bioinformatics-knowledge",
        llm_model="gpt-4",
        enable_code_execution=False,
    )

    # Query for information
    result = await rag.smart_bio_query(
        "How do I perform quality control on single-cell RNA-seq data?", top_k=5
    )

    print("\nAnswer:")
    print(result["answer"])
    print(f"\nSources used: {len(result['sources'])}")
    for i, source in enumerate(result["sources"][:3]):
        print(f"  {i + 1}. {source.get('metadata', {}).get('source', 'unknown')}")


async def example_code_generation():
    """Example: Generate analysis code"""
    print("\n" + "=" * 60)
    print("Example 2: Code Generation")
    print("=" * 60)

    rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path="./rag_databases/bioinformatics-knowledge",
        llm_model="gpt-4",
    )

    # Generate code for a specific task
    result = await rag.generate_bio_code(
        task="Load a 10x genomics dataset, perform QC, normalize, and cluster cells",
        data_path="/data/pbmc3k/filtered_gene_bc_matrices/hg19/",
        execute=False,
    )

    print("\nGenerated Code:")
    print("-" * 40)
    print(result["code"])
    print("-" * 40)
    print(f"\nAnalysis type identified: {result['intent']}")
    print(f"Documentation sources: {result['documentation_used']}")


async def example_complete_analysis():
    """Example: Complete analysis pipeline"""
    print("\n" + "=" * 60)
    print("Example 3: Complete Analysis Pipeline")
    print("=" * 60)

    rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path="./rag_databases/bioinformatics-knowledge",
        llm_model="gpt-4",
        enable_code_execution=True,
    )

    # Complete analysis with auto-execution
    result = await rag.analyze_bio_data(
        query="Analyze this PBMC dataset to identify major immune cell types",
        data_path="./data/pbmc3k.h5ad",
        auto_execute=True,
        iterative=True,  # Try to fix errors automatically
    )

    print("\nAnalysis Code:")
    print("-" * 40)
    print(result["code"][:500] + "...")  # Show first 500 chars
    print("-" * 40)

    if "execution" in result:
        print("\nExecution successful!")
        print(f"Output: {result['execution'][:200]}...")
    elif "execution_error" in result:
        print(f"\nExecution error: {result['execution_error']}")
        if "fixed" in result:
            print("Code was automatically fixed and re-executed.")


async def example_troubleshooting():
    """Example: Troubleshoot an issue"""
    print("\n" + "=" * 60)
    print("Example 4: Troubleshooting")
    print("=" * 60)

    rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path="./rag_databases/bioinformatics-knowledge",
        llm_model="gpt-4",
    )

    # Troubleshoot a common issue
    result = await rag.troubleshoot_bio_issue(
        issue="My UMAP plot shows all cells in one blob",
        error_message=None,
        code_snippet="sc.tl.umap(adata)",
    )

    print("\nTroubleshooting Guide:")
    print(result["troubleshooting_guide"])


async def example_with_agent():
    """Example: Using with a Pantheon agent"""
    print("\n" + "=" * 60)
    print("Example 5: Integration with Pantheon Agent")
    print("=" * 60)

    # Create agent with bio-specific instructions
    agent = Agent(
        name="bio_analyst",
        instructions="""
        You are an expert bioinformatics analyst specializing in single-cell genomics.
        
        When users ask for analysis:
        1. First use smart_bio_query to understand the best methods
        2. Use generate_bio_code to create analysis pipelines
        3. Execute code when appropriate
        4. Explain results clearly with biological interpretation
        
        Always follow best practices and cite your sources.
        """,
        model="gpt-4",
    )

    # Add toolsets
    bio_rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path="./rag_databases/bioinformatics-knowledge",
        llm_model="gpt-4",
    )
    python_tools = PythonInterpreterToolSet("python")

    agent.toolset(bio_rag)
    agent.toolset(python_tools)

    # Run analysis
    response = await agent.run(
        "I have a dataset with 5000 cells. What's the recommended resolution parameter for Leiden clustering?"
    )

    print("\nAgent Response:")
    print(response)


async def interactive_mode():
    """Interactive mode for testing"""
    print("\n" + "=" * 60)
    print("Interactive Mode - Bioinformatics Assistant")
    print("=" * 60)
    print("Type 'exit' to quit\n")

    # Initialize agent
    agent = Agent(
        name="bio_assistant",
        instructions="""
        You are a helpful bioinformatics assistant with access to comprehensive documentation.
        Use the agentic RAG tools to provide accurate, well-sourced answers.
        Generate code when requested and explain complex concepts clearly.
        """,
        model="gpt-4",
    )

    # Add agentic RAG
    bio_rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path="./rag_databases/bioinformatics-knowledge",
        llm_model="gpt-4",
        enable_code_execution=True,
    )
    agent.toolset(bio_rag)

    # Interactive loop
    while True:
        query = input("\nYour question: ")
        if query.lower() in ["exit", "quit"]:
            break

        response = await agent.run(query)
        print("\nAssistant:", response)


async def main():
    """Main function to run examples"""
    import sys

    # First, ensure knowledge base is built
    # await build_knowledge_base()

    if len(sys.argv) > 1:
        example = sys.argv[1]
        if example == "basic":
            await example_basic_query()
        elif example == "code":
            await example_code_generation()
        elif example == "analysis":
            await example_complete_analysis()
        elif example == "troubleshoot":
            await example_troubleshooting()
        elif example == "agent":
            await example_with_agent()
        elif example == "interactive":
            await interactive_mode()
        else:
            print(
                "Unknown example. Choose from: basic, code, analysis, troubleshoot, agent, interactive"
            )
    else:
        # Run all examples
        await example_basic_query()
        await example_code_generation()
        # await example_complete_analysis()  # Skip by default as it needs data
        await example_troubleshooting()
        # await example_with_agent()


if __name__ == "__main__":
    asyncio.run(main())
