Toolsets API
============

Toolsets extend agent capabilities with tools for file operations, code execution, web search, and more.

Using Toolsets
--------------

Add toolsets at runtime using ``await agent.toolset()``:

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets import FileManagerToolSet, ShellToolSet

   agent = Agent(
       name="developer",
       instructions="You are a developer assistant."
   )

   # Add toolsets at runtime (name parameter is required)
   await agent.toolset(FileManagerToolSet("files"))
   await agent.toolset(ShellToolSet("shell"))

Available Toolsets
------------------

**File Operations**

.. code-block:: python

   from pantheon.toolsets import FileManagerToolSet

   # Read, write, edit, search files
   await agent.toolset(FileManagerToolSet("files"))

**Shell Commands**

.. code-block:: python

   from pantheon.toolsets import ShellToolSet

   # Run shell commands with timeout protection
   await agent.toolset(ShellToolSet("shell"))

**Python Execution**

.. code-block:: python

   from pantheon.toolsets import PythonInterpreterToolSet

   # Execute Python code
   await agent.toolset(PythonInterpreterToolSet("python"))

**Jupyter Notebooks**

.. code-block:: python

   from pantheon.toolsets import IntegratedNotebookToolSet

   # Create, edit, execute notebooks
   await agent.toolset(IntegratedNotebookToolSet("notebook"))

**Web Search**

.. code-block:: python

   from pantheon.toolsets import WebToolSet

   # Search and fetch web content
   await agent.toolset(WebToolSet("web"))

**Knowledge Base**

.. code-block:: python

   from pantheon.toolsets import KnowledgeToolSet

   # Vector store and RAG
   await agent.toolset(KnowledgeToolSet("knowledge"))

Creating Custom Tools
---------------------

**Simple Function**

.. code-block:: python

   @agent.tool
   def my_function(param: str) -> str:
       """Description shown to the LLM."""
       return f"Result: {param}"

**ToolSet Class**

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool

   class MyToolSet(ToolSet):
       @tool
       def calculate(self, expression: str) -> float:
           """Evaluate a math expression.

           Args:
               expression: Math expression to evaluate

           Returns:
               The result of the calculation
           """
           return eval(expression)

       @tool
       async def fetch_data(self, url: str) -> str:
           """Fetch data from a URL."""
           async with aiohttp.ClientSession() as session:
               async with session.get(url) as resp:
                   return await resp.text()

Tool Documentation
------------------

The docstring becomes the tool description:

.. code-block:: python

   @tool
   def search_files(self, pattern: str, directory: str = ".") -> list:
       """Search for files matching a pattern.

       Use this tool when you need to find files in the project.

       Args:
           pattern: Glob pattern (e.g., "*.py", "**/*.md")
           directory: Directory to search (default: current directory)

       Returns:
           List of matching file paths
       """
       return glob.glob(pattern, root_dir=directory)

Good docstrings help the LLM understand when and how to use the tool.

Async Tools
-----------

Use async for I/O operations:

.. code-block:: python

   @tool
   async def fetch_url(self, url: str) -> str:
       """Fetch content from a URL."""
       async with aiohttp.ClientSession() as session:
           async with session.get(url) as response:
               return await response.text()

Combining Toolsets
------------------

.. code-block:: python

   agent = Agent(
       name="full_stack",
       instructions="You are a full-stack developer."
   )
   await agent.toolset(FileManagerToolSet("files"))
   await agent.toolset(ShellToolSet("shell"))
   await agent.toolset(PythonInterpreterToolSet("python"))
   await agent.toolset(WebToolSet("web"))

Best Practices
--------------

**Only Include Needed Tools**

Too many tools can confuse the model. Be selective.

**Clear Descriptions**

Write descriptive docstrings that explain when to use each tool.

**Error Handling**

Return informative error messages:

.. code-block:: python

   @tool
   def read_file(self, path: str) -> str:
       """Read a file."""
       try:
           with open(path) as f:
               return f.read()
       except FileNotFoundError:
           return f"Error: File '{path}' not found"
       except PermissionError:
           return f"Error: No permission to read '{path}'"
