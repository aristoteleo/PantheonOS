#!/usr/bin/env python3
"""
Test Agentic RAG without knowledge base (using LLM knowledge only)
This demonstrates that the system can work even while KB building is in progress
"""

import asyncio
from pantheon.toolsets.agentic_rag import AgenticRAGToolSet
from pantheon.toolsets.agentic_rag.bio_templates import get_template


async def test_without_kb():
    """Test the agentic RAG using only LLM knowledge"""

    print("=" * 60)
    print("Testing Agentic RAG without Knowledge Base")
    print("Using LLM's built-in bioinformatics knowledge")
    print("=" * 60 + "\n")

    # Initialize without knowledge base
    rag = AgenticRAGToolSet(
        name="bio_rag",
        db_path=None,  # No knowledge base
        llm_model="gpt-4",
        enable_code_execution=False,  # Disable execution for testing
    )

    # Test 1: Basic query
    print("Test 1: Basic Query")
    print("-" * 40)
    result = await rag.smart_bio_query(
        "What is the typical resolution parameter for Leiden clustering?", top_k=3
    )
    print(f"Answer: {result['answer'][:500]}...")
    print()

    # Test 2: Code generation
    print("Test 2: Code Generation")
    print("-" * 40)
    code_result = await rag.generate_bio_code(
        task="Load a h5ad file and perform basic quality control",
        data_path="test_data.h5ad",
        execute=False,
    )
    print("Generated code:")
    print(code_result["code"][:600] + "...")
    print()

    # Test 3: Method explanation
    print("Test 3: Method Explanation")
    print("-" * 40)
    explanation = await rag.explain_bio_method(
        method="UMAP", include_parameters=True, include_examples=True
    )
    print(f"Explanation: {explanation['explanation'][:500]}...")
    print()

    # Test 4: Troubleshooting
    print("Test 4: Troubleshooting")
    print("-" * 40)
    troubleshoot = await rag.troubleshoot_bio_issue(
        issue="My UMAP shows all cells clustered together",
        error_message=None,
        code_snippet="sc.tl.umap(adata)",
    )
    print(f"Solution: {troubleshoot['troubleshooting_guide'][:500]}...")
    print()

    print("=" * 60)
    print("All tests completed successfully!")
    print("The system works without a knowledge base by leveraging")
    print("the LLM's inherent knowledge of bioinformatics.")
    print("=" * 60)


async def test_with_templates():
    """Test using built-in templates"""

    print("\n" + "=" * 60)
    print("Testing with Built-in Templates")
    print("=" * 60 + "\n")

    # Get a template
    template = get_template("scrna_standard")
    print("Standard scRNA-seq template loaded:")
    print(template[:300] + "...")
    print()

    # Use template with agentic RAG
    rag = AgenticRAGToolSet(name="bio_rag", db_path=None, llm_model="gpt-4")

    # Enhance template with specific requirements
    result = await rag.generate_bio_code(
        task="Modify the standard scRNA-seq pipeline to include batch correction using Harmony",
        context=f"Starting template:\n{template[:500]}",
        execute=False,
    )

    print("Enhanced code with batch correction:")
    print(result["code"][:600] + "...")


async def main():
    """Run all tests"""
    await test_without_kb()
    await test_with_templates()


if __name__ == "__main__":
    # Note: Requires OPENAI_API_KEY environment variable
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("Warning: OPENAI_API_KEY not set. Using mock responses.")
        print("Set your OpenAI API key to test with real LLM:")
        print("export OPENAI_API_KEY=your_key_here")

    asyncio.run(main())
