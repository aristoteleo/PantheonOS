"""Pantheon CLI Core - Main entry point for the CLI assistant"""

import asyncio
import os
from pathlib import Path
from typing import Optional
import fire

# Import toolsets
from pantheon.toolsets.shell import ShellToolSet
from pantheon.toolsets.vector_rag import VectorRAGToolSet
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.file_editor import FileEditorToolSet
from pantheon.toolsets.code_search import CodeSearchToolSet
from pantheon.toolsets.notebook import NotebookToolSet
from pantheon.toolsets.web import WebToolSet
from pantheon.agent import Agent


DEFAULT_INSTRUCTIONS = """
You are a CLI assistant for Single-Cell/Spatial genomics analysis with multiple tool capabilities.

TOOL SELECTION RULES:

Use SHELL commands for:
- System operations: mkdir, cp, mv, rm  
- System information: pwd, whoami, df, ps
- Genomics command-line tools: STAR, kallisto, bustools, etc.

Use PYTHON (run_code tool) for:
- Data analysis and statistics
- Creating plots and visualizations  
- Mathematical calculations
- Programming scripts
- Processing data files (CSV, JSON, etc.)

Use FILE OPERATIONS for:
- read_file: Read file contents with line numbers
- edit_file: Edit files by replacing text (shows diff)
- write_file: Create new files
- search_in_file: Search within ONE specific file (when you already know the exact file)

Use CODE SEARCH for (PREFERRED for search operations):
- glob: Find files by pattern (e.g., "*.py", "**/*.js")
- grep: Search for text across multiple files or in specific file patterns
- ls: List directory contents with details

Use NOTEBOOK operations for Jupyter notebooks:
- read_notebook: Display notebook contents with beautiful formatting
- edit_notebook_cell: Edit specific cells (code/markdown)
- add_notebook_cell: Add new cells at specific positions
- delete_notebook_cell: Remove cells from notebook
- create_notebook: Create new Jupyter notebooks

Use WEB operations for online content:
- web_fetch: Fetch and display web page content (like Claude Code's WebFetch)
- web_search: Search the web using DuckDuckGo (like Claude Code's WebSearch)

SEARCH PRIORITY RULES:
- Use "grep" for ANY content search (even in single files)
- Use "search_in_file" ONLY when specifically asked to search within one known file
- Use "glob" to find files first, then "grep" to search their contents

CRITICAL PYTHON RULE: When using Python, you MUST execute code with run_code tool - never just show code!

Examples:
- "查看当前目录" → Use code_search: ls tool
- "find all Python files" → Use code_search: glob with "*.py"
- "find all notebooks" → Use code_search: glob with "*.ipynb"
- "search for 'import' in code" → Use code_search: grep tool
- "search for TODO in main.py" → Use code_search: grep tool (NOT search_in_file)
- "read config.py" → Use file_editor: read_file tool
- "read analysis.ipynb" → Use notebook: read_notebook tool
- "edit cell 3 in notebook" → Use notebook: edit_notebook_cell tool
- "add code cell to notebook" → Use notebook: add_notebook_cell tool
- "create new notebook" → Use notebook: create_notebook tool
- "calculate fibonacci" → Use Python: run_code tool
- "create a plot" → Use Python: run_code tool
- "run STAR alignment" → Use shell commands
- "analyze expression data" → Use Python: run_code tool
- "查询网页内容" → Use web: web_fetch tool
- "搜索相关信息" → Use web: web_search tool

Workflow:
1. Understand the request type
2. Choose the appropriate tool (shell vs Python vs other)
3. If Python: always execute with run_code
4. If shell: use shell commands directly
5. If need knowledge: search vector database
6. Explain results

Be smart about tool selection - use the right tool for the job!
"""


async def main(
    rag_db: Optional[str] = None,
    model: str = "gpt-4.1",
    agent_name: str = "sc_cli_bot",
    workspace: Optional[str] = None,
    instructions: Optional[str] = None,
    disable_rag: bool = False,
    disable_web: bool = False,
    disable_notebook: bool = False
):
    """
    Start the Pantheon CLI assistant.
    
    Args:
        rag_db: Path to RAG database (default: tmp/sc_cli_tools_rag/single-cell-cli-tools)
        model: Model to use (default: gpt-4.1)
        agent_name: Name of the agent (default: sc_cli_bot)
        workspace: Workspace directory (default: current directory)
        instructions: Custom instructions for the agent (default: built-in instructions)
        disable_rag: Disable RAG toolset
        disable_web: Disable web toolset
        disable_notebook: Disable notebook toolset
    """
    # Set default RAG database path if not provided
    if rag_db is None and not disable_rag:
        default_rag = Path("tmp/sc_cli_tools_rag/single-cell-cli-tools")
        if default_rag.exists():
            rag_db = str(default_rag)
        else:
            print(f"[Warning] Default RAG database not found at {default_rag}")
            print("RAG toolset will be disabled. To enable, provide --rag-db path")
            disable_rag = True
    
    # Set workspace
    workspace_path = Path(workspace) if workspace else Path.cwd()
    
    # Use custom instructions or default
    agent_instructions = instructions or DEFAULT_INSTRUCTIONS
    
    # Initialize toolsets
    shell_toolset = ShellToolSet("shell")
    python_toolset = PythonInterpreterToolSet("python")
    file_editor = FileEditorToolSet("file_editor", workspace_path=workspace_path)
    code_search = CodeSearchToolSet("code_search", workspace_path=workspace_path)
    
    # Optional toolsets
    vector_rag_toolset = None
    if not disable_rag and rag_db:
        vector_rag_toolset = VectorRAGToolSet(
            "vector_rag",
            db_path=rag_db,
        )
    
    notebook = None
    if not disable_notebook:
        notebook = NotebookToolSet("notebook", workspace_path=workspace_path)
    
    web = None
    if not disable_web:
        web = WebToolSet("web")
    
    # Create agent
    agent = Agent(
        agent_name,
        agent_instructions,
        model=model,
    )
    
    # Add toolsets to agent
    agent.toolset(shell_toolset)
    agent.toolset(python_toolset)
    agent.toolset(file_editor)
    agent.toolset(code_search)
    
    if vector_rag_toolset:
        agent.toolset(vector_rag_toolset)
    if notebook:
        agent.toolset(notebook)
    if web:
        agent.toolset(web)
    
    
    await agent.chat()


def cli():
    """Fire CLI entry point"""
    fire.Fire(main)