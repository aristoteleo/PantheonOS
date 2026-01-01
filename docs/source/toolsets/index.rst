Toolsets
========

Toolsets extend agent capabilities by providing access to external functions, APIs, and services. They are bundled with the ``pantheon-agents`` package and can be used directly with agents.

Installation
------------

Toolsets are included with pantheon-agents. Install with the toolsets extra for full functionality::

    pip install pantheon-agents[toolsets]

This includes dependencies for:

- Python code execution
- Jupyter notebook integration
- Web browsing and scraping
- File operations
- Code parsing (tree-sitter)

Overview
--------

Pantheon's toolset system provides a unified interface for extending agent capabilities:

- **Local Execution**: Tools run in the same process as the agent
- **Tool Decorator**: Methods decorated with ``@tool`` are automatically exposed to agents
- **Type Safety**: Automatic parameter validation from type hints
- **Async Support**: Full support for async tool implementations

Architecture
------------

The toolset system consists of:

1. **ToolSet Base Class**: Base class that all toolsets inherit from
2. **Tool Decorator**: Marks methods as tools with docstring descriptions
3. **Provider System**: Handles tool discovery and execution

Built-in Toolsets
-----------------

File Operations
~~~~~~~~~~~~~~~

- **FileManagerToolSet**: Comprehensive file operations

  - ``read_file``: Read file contents
  - ``write_file``: Write/create files
  - ``edit_file``: Smart diff-based editing
  - ``glob``: Pattern-based file search
  - ``grep``: Content search with regex
  - ``patch_file``: Apply unified diff patches

Code Execution
~~~~~~~~~~~~~~

- **PythonInterpreterToolSet**: Execute Python code in isolated environment
- **ShellToolSet**: Run shell commands with timeout and safety controls
- **IntegratedNotebookToolSet**: Jupyter notebook with kernel management

  - Create, read, update notebook cells
  - Execute cells with output capture
  - Kernel lifecycle management

Web & Search
~~~~~~~~~~~~

- **WebToolSet**: Web browsing capabilities

  - ``search``: Web search via DuckDuckGo
  - ``fetch_url``: Fetch and parse web pages

- **ScraperToolSet**: Advanced web scraping with crawl4ai

Knowledge & RAG
~~~~~~~~~~~~~~~

- **KnowledgeToolSet**: Knowledge base management

  - Vector store integration (Qdrant)
  - Document indexing and retrieval
  - LlamaIndex integration

- **VectorRAGToolSet**: Vector-based retrieval augmented generation

Specialized Toolsets
~~~~~~~~~~~~~~~~~~~~

- **TaskToolSet**: Ephemeral task tracking for workflows
- **PackageToolSet**: Python package discovery and method extraction
- **EvolutionToolSet**: Program evolution and optimization tools
- **SkillbookToolSet**: Skill management for learning agents
- **DatabaseAPIQueryToolSet**: Database query capabilities
- **RInterpreterToolSet**: R language code execution
- **JuliaInterpreterToolSet**: Julia language code execution

Quick Start
-----------

Using Built-in Toolsets
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon import Agent
   from pantheon.toolsets import FileManagerToolSet, ShellToolSet

   # Create toolsets
   file_tools = FileManagerToolSet()
   shell_tools = ShellToolSet()

   # Create agent with toolsets
   agent = Agent(
       name="developer",
       instructions="You are a developer assistant.",
       model="gpt-4o",
       tools=[file_tools, shell_tools]
   )

   await agent.chat()

Creating Custom Tools
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon import ToolSet, tool

   class MyToolSet(ToolSet):
       @tool
       def calculate(self, expression: str) -> float:
           """Evaluate a mathematical expression.

           Args:
               expression: The mathematical expression to evaluate

           Returns:
               The result of the calculation
           """
           return eval(expression)

       @tool
       async def fetch_data(self, url: str) -> str:
           """Fetch data from a URL asynchronously."""
           import aiohttp
           async with aiohttp.ClientSession() as session:
               async with session.get(url) as response:
                   return await response.text()

   # Use with agent
   agent = Agent(
       name="assistant",
       tools=[MyToolSet()]
   )

Tool Decorator Options
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolset import tool, JobMode

   class AdvancedToolSet(ToolSet):
       @tool(job_mode=JobMode.PROCESS)
       def cpu_intensive_task(self, data: str) -> str:
           """Run in separate process for CPU-intensive work."""
           # Heavy computation here
           return result

       @tool(job_mode=JobMode.THREAD)
       def io_bound_task(self, path: str) -> str:
           """Run in thread pool for I/O operations."""
           # I/O operations here
           return result

Best Practices
--------------

1. **Clear Docstrings**: Write descriptive docstrings - they become the tool description for the LLM
2. **Type Hints**: Always use type hints for parameters and return values
3. **Error Handling**: Handle errors gracefully and return informative error messages
4. **Async When Possible**: Use async for I/O-bound operations
5. **Resource Cleanup**: Implement cleanup in toolset lifecycle methods

MCP Integration
---------------

Toolsets can be exposed as MCP (Model Context Protocol) servers::

   from pantheon.endpoint.mcp import MCPGateway
   from pantheon.toolsets import FileManagerToolSet

   gateway = MCPGateway()
   gateway.add_toolset(FileManagerToolSet())
   await gateway.serve()

See :doc:`/api/utils` for more details on MCP integration.

.. toctree::
   :hidden:
   :maxdepth: 1

   builtin_toolsets
   custom_toolsets
   file_editor
   notebook
   python_interpreter
   r_interpreter
   shell
   web_browse
   scraper_api
   rag_system
   vector_rag
