Toolsets Reference
==================

Toolsets extend agent capabilities by providing access to external functions, APIs, and services. They are bundled with the ``pantheon-agents`` package and can be used directly with agents.

Installation
------------

Toolsets are included with the core ``pantheon-agents`` package::

    pip install pantheon-agents

All toolset dependencies are included:

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

Complete Toolset Catalog
------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Toolset
     - Category
     - Key Capabilities
   * - :doc:`python_interpreter`
     - Code Execution
     - Persistent Python sessions, run code, manage interpreters
   * - :doc:`r_interpreter`
     - Code Execution
     - Persistent R sessions
   * - :doc:`julia_interpreter`
     - Code Execution
     - Persistent Julia sessions
   * - :doc:`shell`
     - Code Execution
     - Shell commands, background processes, timeouts
   * - :doc:`notebook`
     - Code Execution
     - Jupyter notebooks, cell ops, kernel management
   * - :doc:`file_editor`
     - Files
     - Read/write/edit files, search, patch, PDF, images
   * - :doc:`file_transfer`
     - Files
     - Chunked streaming file I/O
   * - :doc:`web_browse`
     - Web & Search
     - DuckDuckGo search, URL crawl/extract
   * - :doc:`scraper_api`
     - Web & Search
     - JS-rendered scraping, selectors, pagination
   * - :doc:`fleet`
     - Infrastructure
     - Multi-node execution, file transfer, cluster management
   * - :doc:`desktop`
     - Visualization
     - Atrium desktop windows, app viewers, and a shared browser
   * - :doc:`scfm`
     - Bioinformatics
     - Single-cell foundation models: annotate, embed, perturb
   * - :doc:`evolution`
     - AI/ML
     - LLM-guided evolutionary code optimization
   * - :doc:`evaluator`
     - AI/ML
     - Code evaluation, metrics, LLM review
   * - :doc:`knowledge`
     - Knowledge & RAG
     - Qdrant hybrid search, collection management
   * - :doc:`vector_rag`
     - Knowledge & RAG
     - LanceDB vector retrieval
   * - :doc:`database_api`
     - Data
     - 26+ biological database queries via natural language
   * - :doc:`scraper_api`
     - Data
     - Web scraping with ScraperAPI
   * - :doc:`image_generation`
     - Media
     - AI image generation (DALL-E, Gemini, …)
   * - :doc:`task`
     - Workflow
     - Task boundaries, user notifications, output registration
   * - :doc:`skillbook`
     - Learning
     - Skill CRUD, trajectory tracking, effectiveness tagging
   * - :doc:`package`
     - Discovery
     - Search packages and tools by keyword
   * - :doc:`code_toolset`
     - Code Analysis
     - AST-based code outline and item extraction
   * - :doc:`custom_toolsets`
     - Extensibility
     - Build your own toolsets

Built-in Toolsets
-----------------

File Operations
~~~~~~~~~~~~~~~

- :doc:`file_editor` - Comprehensive file operations

  - ``read_file``: Read file contents with optional line range
  - ``write_file``: Write/create files
  - ``update_file``: String replacement editing
  - ``manage_path``: Create/delete/move files and directories
  - ``glob``: Pattern-based file search
  - ``grep``: Content search with regex
  - ``apply_patch``: Apply unified diff or V4A patches
  - ``observe_images``: Analyze images with LLM vision
  - ``read_pdf``: Extract text from PDFs

- :doc:`file_transfer` - Chunked file transfer with streaming support

  - ``open_file_for_write``: Get file handle for writing
  - ``write_chunk``: Write data chunks to open file
  - ``close_file``: Close file handle
  - ``read_file``: Read file with base64 encoding

Code Execution
~~~~~~~~~~~~~~

- :doc:`python_interpreter` - Execute Python code in isolated environment

  - ``run_python_code``: Execute Python with auto session management
  - ``manage_interpreters``: Create, list, delete interpreter sessions

- :doc:`shell` - Run shell commands with timeout support

  - ``run_command``: Execute shell commands
  - ``get_shell_output``: Fetch output from background commands
  - ``close_shell``: Close shell sessions

- :doc:`r_interpreter` - Execute R code with session management

  - ``run_r_code``: Execute R code with auto session management
  - ``new_interpreter``, ``delete_interpreter``: Interpreter management

- :doc:`julia_interpreter` - Execute Julia code with session management

  - ``run_julia_code``: Execute Julia code with interpreter management
  - ``new_interpreter``, ``delete_interpreter``: Interpreter management

- :doc:`notebook` - Jupyter notebook with kernel management

  - ``create_notebook``: Create or open notebooks
  - ``add_cell``, ``update_cell``, ``delete_cell``: Cell operations
  - ``execute_cell``: Execute cells with output capture
  - ``manage_kernel``: Restart, interrupt, shutdown kernels

Code Analysis
~~~~~~~~~~~~~

- :doc:`code_toolset` - Code navigation with tree-sitter AST analysis

  - ``view_file_outline``: Get structural outline of code files
  - ``view_code_item``: Extract specific code items by name

Web & Search
~~~~~~~~~~~~

- :doc:`web_browse` - Web search and content retrieval

  - ``duckduckgo_search``: Web search via DuckDuckGo
  - ``web_crawl``: Fetch and extract content from URLs as markdown

- :doc:`scraper_api` - Advanced web scraping with JavaScript rendering

  - ``scrape_url``: Scrape with CSS/XPath selectors
  - Supports pagination, infinite scroll, screenshots

Knowledge & RAG
~~~~~~~~~~~~~~~

- :doc:`knowledge` - Knowledge base management with hybrid search

  - ``search_knowledge``: Semantic + keyword search with Qdrant
  - ``create_collection``, ``delete_collection``: Collection management
  - ``add_source``, ``remove_source``: Document source management
  - ``index_source``: Index documents into vector store

- :doc:`vector_rag` - Vector-based retrieval augmented generation

  - Vector store integration
  - Document indexing and retrieval

Media
~~~~~

- :doc:`image_generation` - AI image generation

  - ``generate_image``: Create images with DALL-E, Gemini, or other providers
  - Supports text prompts and image references

Database
~~~~~~~~

- :doc:`database_api` - Biological database queries (26+ databases)

  - ``query``: Query any supported database
  - ``list_databases``: List available databases
  - ``database_info``: Get database schema and examples

Workflow & Learning
~~~~~~~~~~~~~~~~~~~

- :doc:`task` - Modal workflow management

  - ``task_boundary``: Track task progress and mode transitions
  - ``notify_user``: User communication with confidence scoring

- :doc:`skillbook` - Skill management for learning agents

  - ``add_skill``, ``update_skill``, ``remove_skill``: Skill CRUD
  - ``tag_skill``: Track skill effectiveness (helpful/harmful)
  - ``list_skills``: Search and filter skills

- :doc:`package` - Package and tool discovery

  - ``search_tools``: Keyword or semantic search for tools
  - ``search_packages``: List and filter available packages

Evolution & Evaluation
~~~~~~~~~~~~~~~~~~~~~~

- :doc:`evolution` - Evolutionary code optimization

  - ``evolve_code``: Optimize single code files
  - ``evolve_codebase``: Optimize multi-file projects
  - ``get_evolution_status``: Monitor evolution progress

- :doc:`evaluator` - Code evaluation and quality assessment

  - ``evaluate_code``: Run custom evaluators on code
  - ``evaluate_codebase``: Evaluate entire projects
  - ``compute_code_metrics``: Static code metrics
  - ``get_llm_code_review``: AI-powered code review

Infrastructure & Visualization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- :doc:`fleet` - Multi-node cluster management

  - ``fleet_list_nodes``, ``fleet_node_info``, ``fleet_status``: Observe cluster
  - ``run_on_node``, ``run_on_label``: Execute on remote nodes
  - ``transfer``, ``broadcast``, ``gather``: Move files across nodes

- :doc:`desktop` - Atrium desktop windows and app viewers

  - ``desktop_apps``, ``desktop_windows``: See what is installed and what is open
  - ``desktop_open``: Open IGV, Vitessce, Mol*, Gosling, Cytoscape, and more
  - ``desktop_read``, ``desktop_update``, ``desktop_set``, ``desktop_call``: Steer a window
  - ``browser_open`` and the ``browser_*`` tools: Drive a shared Chromium page

- :doc:`scfm` - Single-cell foundation models

  - ``scfm_list_models``, ``scfm_describe_model``: Browse available models
  - ``scfm_select_model``, ``scfm_run``: Run annotation, embedding, perturbation
  - ``scfm_interpret_results``: Summarize and explain model output

Quick Start
-----------

Using Built-in Toolsets
~~~~~~~~~~~~~~~~~~~~~~~

**Method 1: In Constructor**

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets import FileManagerToolSet, ShellToolSet

   # Create toolsets
   file_tools = FileManagerToolSet(name="files")
   shell_tools = ShellToolSet(name="shell")

   # Create agent and add toolsets at runtime
   agent = Agent(
       name="developer",
       instructions="You are a developer assistant."
   )
   await agent.toolset(file_tools)
   await agent.toolset(shell_tools)

   await agent.chat()

**Alternative: Chained calls**

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets import FileManagerToolSet, ShellToolSet

   # Create agent first
   agent = Agent(
       name="developer",
       instructions="You are a developer assistant."
   )

   # Add toolsets dynamically
   await agent.toolset(FileManagerToolSet(name="files"))
   await agent.toolset(ShellToolSet(name="shell"))

   await agent.chat()

Creating Custom Tools
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolset import ToolSet, tool

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
       instructions="You are a helpful assistant."
   )
   await agent.toolset(MyToolSet(name="my_tools"))

Tool Decorator Options
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolset import tool

   class AdvancedToolSet(ToolSet):
       @tool
       def cpu_intensive_task(self, data: str) -> str:
           """CPU-intensive work."""
           # Heavy computation here
           return result

       @tool
       def io_bound_task(self, path: str) -> str:
           """I/O operations."""
           # I/O operations here
           return result

Creating Custom ToolSets
------------------------

Need specialized functionality? Create custom toolsets by extending the base ``ToolSet`` class. See :doc:`custom_toolset` for a complete guide with examples including:

* Session management for multi-user scenarios
* Accessing execution context
* Lifecycle methods (setup/cleanup)
* Calling the LLM from within tools

.. code-block:: python

   from pantheon.toolset import ToolSet, tool

   class TodoToolSet(ToolSet):
       def __init__(self, name: str):
           super().__init__(name)
           self.todos = {}

       @tool
       async def add_todo(self, title: str, priority: str = "medium") -> dict:
           """Add a new todo item.

           Args:
               title: The todo item title
               priority: Priority level (low, medium, high)
           """
           session_id = self.get_session_id()
           # Per-session storage
           if session_id not in self.todos:
               self.todos[session_id] = []
           todo = {"id": len(self.todos[session_id]) + 1, "title": title}
           self.todos[session_id].append(todo)
           return {"success": True, "todo": todo}

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
   gateway.add_toolset(FileManagerToolSet("files"))
   await gateway.serve()

See :doc:`/api/utils` for more details on MCP integration.

