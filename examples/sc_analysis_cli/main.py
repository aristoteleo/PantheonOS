import fire
from pantheon.toolsets.scraper import ScraperToolSet
from pantheon.toolsets.shell import ShellToolSet
from pantheon.toolsets.vector_rag import VectorRAGToolSet
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.agent import Agent


async def main(path_to_rag_db: str):
    scraper_toolset = ScraperToolSet("scraper")
    shell_toolset = ShellToolSet("shell")
    python_toolset = PythonInterpreterToolSet("python")
    vector_rag_toolset = VectorRAGToolSet(
        "vector_rag",
        db_path=path_to_rag_db,
    )

    instructions = """
    You are a CLI assistant for Single-Cell/Spatial genomics analysis with multiple tool capabilities.
    
    TOOL SELECTION RULES:
    
    Use SHELL commands for:
    - File/directory operations: ls, mkdir, cp, mv, rm
    - System information: pwd, whoami, df, ps
    - Text processing: cat, grep, head, tail, wc
    - Genomics command-line tools: STAR, kallisto, bustools, etc.
    
    Use PYTHON (run_code tool) for:
    - Data analysis and statistics
    - Creating plots and visualizations  
    - Mathematical calculations
    - Programming scripts
    - Processing data files (CSV, JSON, etc.)
    
    CRITICAL PYTHON RULE: When using Python, you MUST execute code with run_code tool - never just show code!
    
    Examples:
    - "查看当前目录" → Use shell: ls or pwd
    - "list files" → Use shell: ls -la
    - "calculate fibonacci" → Use Python: run_code tool
    - "create a plot" → Use Python: run_code tool
    - "run STAR alignment" → Use shell commands
    - "analyze expression data" → Use Python: run_code tool
    
    Workflow:
    1. Understand the request type
    2. Choose the appropriate tool (shell vs Python vs other)
    3. If Python: always execute with run_code
    4. If shell: use shell commands directly
    5. If need knowledge: search vector database
    6. Explain results
    
    Be smart about tool selection - use the right tool for the job!
    """

    agent = Agent(
        "sc_cli_bot",
        instructions,
        model="gpt-4.1",
    )
    agent.toolset(scraper_toolset)
    agent.toolset(shell_toolset)
    agent.toolset(python_toolset)
    agent.toolset(vector_rag_toolset)

    await agent.chat()


if __name__ == "__main__":
    fire.Fire(main)