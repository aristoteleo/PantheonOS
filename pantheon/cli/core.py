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
from pantheon.toolsets.r import RInterpreterToolSet
from pantheon.toolsets.file_editor import FileEditorToolSet
from pantheon.toolsets.code_search import CodeSearchToolSet
from pantheon.toolsets.notebook import NotebookToolSet
from pantheon.toolsets.web import WebToolSet
from pantheon.toolsets.todo import TodoToolSet
from pantheon.toolsets.code_validator import CodeValidatorToolSet
from pantheon.agent import Agent


DEFAULT_INSTRUCTIONS = """
You are a CLI assistant for Single-Cell/Spatial genomics analysis with multiple tool capabilities.

⚠️  CRITICAL: You have BOTH Python and R interpreters available!
- Use run_python for: pandas, numpy, matplotlib, scanpy
- Use run_r for: Seurat, ggplot2, single-cell RNA-seq analysis

TOOL SELECTION RULES:

Use SHELL commands for:
- System operations: mkdir, cp, mv, rm  
- System information: pwd, whoami, df, ps
- Genomics command-line tools: STAR, kallisto, bustools, etc.

Use PYTHON (run_python tool) for:
- Data analysis and statistics with pandas, numpy
- Creating plots and visualizations with matplotlib, seaborn
- Mathematical calculations and machine learning
- Programming scripts and automation
- Processing data files (CSV, JSON, etc.)
- Python-based single-cell analysis (scanpy, anndata)

Use R (run_r tool) for:
- Single-cell RNA-seq analysis with Seurat
- Statistical analysis and modeling
- Bioconductor packages and workflows  
- ggplot2 visualizations and publication-ready plots
- Load sample data with: load_sample_data('pbmc3k')
- Quick Seurat workflow: quick_seurat_analysis(seurat_obj)
- Auto-save figures: auto_ggsave()

Use FILE OPERATIONS for:
- read_file: Read file contents with line numbers
- edit_file: Edit files by replacing text (shows diff)
- write_file: Create new files
- search_in_file: Search within ONE specific file (when you already know the exact file)

Use CODE SEARCH for (PREFERRED for search operations):
- glob: Find files by pattern (e.g., "*.py", "**/*.js")
- grep: Search for text across multiple files or in specific file patterns
- ls: List directory contents with details

Use CODE VALIDATION for verifying generated code:
- validate_python_code: Check Python code syntax, imports, and functions
- validate_command: Verify shell commands and parameters using help
- validate_function_call: Check if functions exist and have correct signatures (with auto-suggestions)
- validate_imports: Test import statements and suggest alternatives
- check_code_style: Analyze code style and provide improvement suggestions
- detect_common_errors: Find common coding errors like redundant parameters, AnnData method mistakes, self parameter errors
- suggest_function_alternatives: Find similar functions when a function doesn't exist, using help() and module inspection

Use NOTEBOOK operations for Jupyter notebooks:
- read_notebook: Display notebook contents with beautiful formatting
- edit_notebook_cell: Edit specific cells (code/markdown)
- add_notebook_cell: Add new cells at specific positions
- delete_notebook_cell: Remove cells from notebook
- create_notebook: Create new Jupyter notebooks

Use WEB operations for online content:
- web_fetch: Fetch and display web page content (like Claude Code's WebFetch)
- web_search: Search the web using DuckDuckGo (like Claude Code's WebSearch)

Use TODO operations for task management:
- add_todo: Add new todo items to track progress (auto-breaks down complex tasks and starts first task)
- show_todos: Display current todos in Claude Code style
- execute_current_task: Analyze current task and get tool suggestions (SMART GUIDANCE!)
- mark_task_done: SIMPLE way to mark current task completed ☑ and move to next (USE THIS!)
- complete_current_todo: Mark current task as completed and move to next (more detailed)
- work_on_next_todo: Start working on the next pending task
- clear_all_todos: Remove all todos to start fresh (prevents duplicates)
- clear_completed_todos: Remove only completed todos
- update_todo_status: Change todo status (pending/in_progress/completed)
- complete_todo: Mark a todo as completed
- start_todo: Mark a todo as in progress

SEARCH PRIORITY RULES:
- Use "grep" for ANY content search (even in single files)
- Use "search_in_file" ONLY when specifically asked to search within one known file
- Use "glob" to find files first, then "grep" to search their contents

CRITICAL EXECUTION RULES:
- For Seurat analysis: ALWAYS use run_r tool - NEVER run_python tool!
- When using Python: MUST execute code with run_python tool - never just show code!  
- When using R: MUST execute code with run_r tool - never just show code!
- Both Python and R have enhanced environments with auto-figure saving

TOOL SELECTION PRIORITY FOR SINGLE-CELL ANALYSIS:
- Seurat, single-cell RNA-seq, scRNA-seq → run_r tool
- scanpy, anndata, Python single-cell → run_python tool

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
- "validate this Python code" → Use validate_python_code tool
- "check if this command is valid" → Use validate_command tool
- "verify numpy.array function" → Use validate_function_call tool
- "check these imports" → Use validate_imports tool
- "analyze code style" → Use check_code_style tool
- "find errors in this code" → Use detect_common_errors tool
- "check for common mistakes" → Use detect_common_errors tool
- "suggest alternatives for this function" → Use suggest_function_alternatives tool
- "what functions are available in this module" → Use suggest_function_alternatives tool
- "calculate fibonacci" → Use run_python tool
- "create a plot" → Use run_python tool (matplotlib) or run_r tool (ggplot2)
- "run STAR alignment" → Use shell commands
- "analyze expression data" → Use run_python tool (scanpy) or run_r tool (Seurat)
- "single-cell analysis with Seurat" → Use run_r tool with load_sample_data() and quick_seurat_analysis()
- "analysis single cell using seurat" → Use run_r tool
- "使用seurat分析单细胞" → Use run_r tool
- "could you analysis the single cell using seurat" → Use run_r tool
- "查询网页内容" → Use web: web_fetch tool
- "搜索相关信息" → Use web: web_search tool
- "add a todo to analyze data" → Use add_todo tool
- "show my todos" → Use show_todos tool
- "mark first todo as completed" → Use complete_todo tool

TODO WORKFLOW - Make CLI SMART, NOT LAZY:
When user adds a todo (like "generate figure step by step"):
1. ALWAYS add the todo first (it auto-breaks down and starts first task)
2. Check execute_current_task to get task analysis and tool suggestions
3. Use the appropriate suggested tool to accomplish the task
4. After successful execution: ALWAYS use mark_task_done() to mark complete and move to next
5. REPEAT until all tasks are done or manual intervention needed
6. Be PROACTIVE - but flexible in execution approach!

CRITICAL RULE: After ANY tool execution that completes a task, you MUST:
- Call mark_task_done() to mark it done ☑ and show updated todo list with checkmarks
- This applies to ALL tools: run_python, run_r, shell, grep, glob, ls, read_file, edit_file, web_fetch, web_search, etc.
- This triggers automatic progression to the next task
- Never leave a task in progress ◐ if it's actually completed!
- ALWAYS use mark_task_done() after ANY successful tool execution that accomplishes a task!

INTELLIGENT EXECUTION:
- execute_current_task() provides task analysis and tool suggestions
- Use your judgment to choose the best approach based on suggestions
- Don't rely on hardcoded solutions - adapt to the specific task context

General Workflow:
1. Understand the request type
2. Choose the appropriate tool (shell vs Python vs R vs file operations vs web vs search)
3. Execute the tool to accomplish the task
4. IMMEDIATELY call mark_task_done() after successful tool execution
5. Continue with next task automatically
6. If need knowledge: search vector database
7. If todo added: IMMEDIATELY start working on it (don't just list it!)
8. Explain results

TOOL EXECUTION EXAMPLES WITH TODO MARKING:
- Run Python code → mark_task_done("Python analysis completed")
- Execute shell command → mark_task_done("Shell command executed")
- Search files with grep → mark_task_done("File search completed")  
- Read/edit files → mark_task_done("File operation completed")
- Web fetch/search → mark_task_done("Web research completed")
- Load data → mark_task_done("Data loading completed")
- Create plot → mark_task_done("Visualization created")

Be smart about tool selection - use the right tool for the job!
CRITICAL: Todo system should make you MORE productive, not just a list maker!
"""


async def main(
    rag_db: Optional[str] = None,
    model: str = "gpt-4.1",
    agent_name: str = "sc_cli_bot",
    workspace: Optional[str] = None,
    instructions: Optional[str] = None,
    disable_rag: bool = False,
    disable_web: bool = False,
    disable_notebook: bool = False,
    disable_r: bool = False,
    disable_code_validator: bool = False
):
    """
    Start the Pantheon CLI assistant.
    
    Args:
        rag_db: Path to RAG database (default: tmp/pantheon_cli_tools_rag/pantheon-cli-tools)
        model: Model to use (default: gpt-4.1)
        agent_name: Name of the agent (default: sc_cli_bot)
        workspace: Workspace directory (default: current directory)
        instructions: Custom instructions for the agent (default: built-in instructions)
        disable_rag: Disable RAG toolset
        disable_web: Disable web toolset
        disable_notebook: Disable notebook toolset
        disable_r: Disable R interpreter toolset
        disable_code_validator: Disable code validator toolset
    """
    # Set default RAG database path if not provided
    if rag_db is None and not disable_rag:
        default_rag = Path("tmp/pantheon_cli_tools_rag/pantheon-cli-tools")
        if default_rag.exists():
            rag_db = str(default_rag)
        else:
            print(f"[Warning] Default RAG database not found at {default_rag}")
            print("Run: python -m pantheon.toolsets.utils.rag build pantheon/cli/rag_system_config.yaml tmp/pantheon_cli_tools_rag")
            print("RAG toolset will be disabled. To enable, provide --rag-db path")
            disable_rag = True
    
    # Set workspace
    workspace_path = Path(workspace) if workspace else Path.cwd()
    
    # Use custom instructions or default
    agent_instructions = instructions or DEFAULT_INSTRUCTIONS
    
    # Initialize toolsets
    shell_toolset = ShellToolSet("shell")
    python_toolset = PythonInterpreterToolSet("python_interpreter", workdir=str(workspace_path))
    file_editor = FileEditorToolSet("file_editor", workspace_path=workspace_path)
    code_search = CodeSearchToolSet("code_search", workspace_path=workspace_path)
    todo_toolset = TodoToolSet("todo", workspace_path=workspace_path)
    
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
    
    r_interpreter = None
    if not disable_r:
        r_interpreter = RInterpreterToolSet("r_interpreter", workdir=str(workspace_path))
    
    code_validator = None
    if not disable_code_validator:
        code_validator = CodeValidatorToolSet("code_validator")
    
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
    agent.toolset(todo_toolset)
    
    if vector_rag_toolset:
        agent.toolset(vector_rag_toolset)
    if notebook:
        agent.toolset(notebook)
    if web:
        agent.toolset(web)
    if r_interpreter:
        agent.toolset(r_interpreter)
    if code_validator:
        agent.toolset(code_validator)
    
    
    await agent.chat()


def cli():
    """Fire CLI entry point"""
    fire.Fire(main)