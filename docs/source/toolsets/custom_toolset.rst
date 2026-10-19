Creating Custom ToolSets
========================

This guide explains how to create custom toolsets by extending the base ``ToolSet`` class to provide agents with specialized capabilities.

Overview
--------

Custom toolsets allow you to:

* Expose domain-specific functionality to agents
* Manage stateful resources (databases, sessions, connections)
* Integrate with external APIs and services
* Share tools across multiple agents

Base Class
----------

All toolsets inherit from the ``ToolSet`` base class:

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool

   class ToolSet(ABC):
       def __init__(self, name: str, **kwargs):
           self._service_name = name
           self._functions = {}  # Auto-collected @tool methods

       async def run_setup(self):
           """Optional async setup before tools are used."""
           pass

       async def cleanup(self):
           """Optional cleanup when toolset is stopped."""
           pass

Creating a Custom ToolSet
-------------------------

Basic Structure
~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool

   class MyToolSet(ToolSet):
       def __init__(self, name: str, my_param: str = "default"):
           super().__init__(name)
           self.my_param = my_param

       @tool
       async def my_tool(self, input: str) -> str:
           """Description shown to the LLM.

           Args:
               input: What this parameter does

           Returns:
               What this tool returns
           """
           return f"Processed: {input}"

The @tool Decorator
-------------------

The ``@tool`` decorator marks methods as tools available to agents:

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool

   class ExampleToolSet(ToolSet):
       @tool
       async def async_tool(self, query: str) -> dict:
           """Async tools are preferred for I/O operations."""
           return {"result": query}

       @tool
       def sync_tool(self, value: int) -> int:
           """Sync tools work too - automatically wrapped as async."""
           return value * 2

       @tool(exclude=True)
       async def internal_tool(self) -> str:
           """Excluded tools are not exposed to LLM agents.

           Use for internal/frontend-only functionality.
           """
           return "internal result"

Decorator Options
~~~~~~~~~~~~~~~~~

.. code-block:: python

   @tool                    # Basic tool, exposed to LLM
   @tool(exclude=True)      # Hidden from LLM, available programmatically

Docstrings as Descriptions
~~~~~~~~~~~~~~~~~~~~~~~~~~

Tool docstrings become the tool description for the LLM:

.. code-block:: python

   @tool
   async def search_database(
       self,
       query: str,
       limit: int = 10,
       include_metadata: bool = False
   ) -> list[dict]:
       """Search the database for matching records.

       Use this tool when you need to find records based on a query.
       Results are sorted by relevance.

       Args:
           query: The search query string
           limit: Maximum number of results to return
           include_metadata: Whether to include metadata in results

       Returns:
           List of matching records with id, name, and score
       """
       # Implementation
       pass

Type Hints
~~~~~~~~~~

Always use type hints - they are used for parameter validation:

.. code-block:: python

   from typing import Optional

   @tool
   async def process_data(
       self,
       data: list[dict],              # Complex types supported
       format: str = "json",          # Default values work
       config: Optional[dict] = None  # Optional parameters
   ) -> dict:
       """Process data with specified format."""
       pass

Accessing Context
-----------------

Tools can access execution context (client ID, session info):

Method 1: Explicit Parameter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool, ExecutionContext

   class MyToolSet(ToolSet):
       @tool
       async def my_tool(
           self,
           query: str,
           context_variables: ExecutionContext  # or ctx, or context
       ) -> str:
           """Tool with explicit context access."""
           client_id = context_variables.get("client_id")
           return f"Client {client_id}: {query}"

Method 2: Implicit Access
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool, get_current_context_variables

   class MyToolSet(ToolSet):
       @tool
       async def my_tool(self, query: str) -> str:
           """Tool with implicit context access."""
           ctx = get_current_context_variables()
           client_id = ctx.get("client_id", "default")
           return f"Client {client_id}: {query}"

       @tool
       async def another_tool(self, data: str) -> str:
           """Using helper method."""
           session_id = self.get_session_id()  # Built-in helper
           return f"Session {session_id}: {data}"

Calling the Agent from Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tools can call back to the LLM for intermediate processing:

.. code-block:: python

   @tool
   async def analyze_with_llm(
       self,
       data: str,
       context: ExecutionContext
   ) -> str:
       """Analyze data using LLM assistance."""
       # Call the agent for intermediate sampling
       summary = await context.call_agent(
           messages=[{"role": "user", "content": f"Summarize: {data}"}],
           system_prompt="You are a summarization expert."
       )
       return f"Summary: {summary}"

Session Management
------------------

Manage per-client state using session IDs:

.. code-block:: python

   class StatefulToolSet(ToolSet):
       def __init__(self, name: str):
           super().__init__(name)
           self.sessions = {}  # client_id -> session state

       @tool
       async def set_value(self, key: str, value: str) -> str:
           """Set a value in the current session."""
           session_id = self.get_session_id()
           if session_id not in self.sessions:
               self.sessions[session_id] = {}
           self.sessions[session_id][key] = value
           return f"Set {key}={value}"

       @tool
       async def get_value(self, key: str) -> str:
           """Get a value from the current session."""
           session_id = self.get_session_id()
           session = self.sessions.get(session_id, {})
           return session.get(key, "Not found")

Lifecycle Methods
-----------------

run_setup()
~~~~~~~~~~~

Called once before tools are used:

.. code-block:: python

   class DatabaseToolSet(ToolSet):
       def __init__(self, name: str, connection_string: str):
           super().__init__(name)
           self.connection_string = connection_string
           self.db = None

       async def run_setup(self):
           """Initialize database connection."""
           import aiosqlite
           self.db = await aiosqlite.connect(self.connection_string)

       @tool
       async def query(self, sql: str) -> list:
           """Execute a SQL query."""
           cursor = await self.db.execute(sql)
           return await cursor.fetchall()

       async def cleanup(self):
           """Close database connection."""
           if self.db:
               await self.db.close()

Complete Example
----------------

A full-featured toolset for managing a todo list:

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool
   from typing import Optional
   from datetime import datetime

   class TodoToolSet(ToolSet):
       """A toolset for managing todo items."""

       def __init__(self, name: str, storage_path: str = "./todos.json"):
           super().__init__(name)
           self.storage_path = storage_path
           self.todos = {}  # session_id -> list of todos

       async def run_setup(self):
           """Load existing todos from storage."""
           import json
           from pathlib import Path

           path = Path(self.storage_path)
           if path.exists():
               with open(path) as f:
                   self.todos = json.load(f)

       async def cleanup(self):
           """Save todos to storage."""
           import json

           with open(self.storage_path, "w") as f:
               json.dump(self.todos, f, indent=2)

       def _get_todos(self) -> list:
           """Get todos for current session."""
           session_id = self.get_session_id()
           if session_id not in self.todos:
               self.todos[session_id] = []
           return self.todos[session_id]

       @tool
       async def add_todo(
           self,
           title: str,
           priority: str = "medium",
           due_date: Optional[str] = None
       ) -> dict:
           """Add a new todo item.

           Args:
               title: The todo item title
               priority: Priority level (low, medium, high)
               due_date: Optional due date in YYYY-MM-DD format

           Returns:
               The created todo item
           """
           todos = self._get_todos()
           todo = {
               "id": len(todos) + 1,
               "title": title,
               "priority": priority,
               "due_date": due_date,
               "completed": False,
               "created_at": datetime.now().isoformat()
           }
           todos.append(todo)
           return {"success": True, "todo": todo}

       @tool
       async def list_todos(
           self,
           show_completed: bool = False
       ) -> dict:
           """List all todo items.

           Args:
               show_completed: Whether to include completed items
           """
           todos = self._get_todos()
           if not show_completed:
               todos = [t for t in todos if not t["completed"]]
           return {"success": True, "todos": todos, "count": len(todos)}

       @tool
       async def complete_todo(self, todo_id: int) -> dict:
           """Mark a todo item as completed.

           Args:
               todo_id: The ID of the todo to complete
           """
           todos = self._get_todos()
           for todo in todos:
               if todo["id"] == todo_id:
                   todo["completed"] = True
                   return {"success": True, "todo": todo}
           return {"success": False, "error": f"Todo {todo_id} not found"}

       @tool
       async def delete_todo(self, todo_id: int) -> dict:
           """Delete a todo item.

           Args:
               todo_id: The ID of the todo to delete
           """
           todos = self._get_todos()
           for i, todo in enumerate(todos):
               if todo["id"] == todo_id:
                   deleted = todos.pop(i)
                   return {"success": True, "deleted": deleted}
           return {"success": False, "error": f"Todo {todo_id} not found"}

Using Custom ToolSets
---------------------

With Agents
~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent

   # Create toolset
   todo_tools = TodoToolSet(name="todos", storage_path="./my_todos.json")

   # Create agent and add toolset at runtime
   agent = Agent(
       name="assistant",
       instructions="You help manage todo lists."
   )
   await agent.toolset(todo_tools)

   await agent.chat()

Multiple ToolSets
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets import FileManagerToolSet, ShellToolSet

   file_tools = FileManagerToolSet("files")
   shell_tools = ShellToolSet("shell")
   todo_tools = TodoToolSet("todos")

   # Create agent and add toolsets at runtime
   agent = Agent(
       name="developer",
       instructions="You are a developer assistant."
   )
   await agent.toolset(file_tools)
   await agent.toolset(shell_tools)
   await agent.toolset(todo_tools)

As MCP Server
~~~~~~~~~~~~~

Convert your toolset to an MCP server:

.. code-block:: python

   # Serve as MCP
   toolset = TodoToolSet(name="todos")
   await toolset.run_as_mcp(transport="http")

   # Or get FastMCP instance for customization
   mcp = toolset.to_mcp()

Best Practices
--------------

1. **Clear docstrings**: Write detailed descriptions - they guide the LLM
2. **Use type hints**: Always specify types for validation
3. **Return structured data**: Return dicts with ``success`` and descriptive fields
4. **Handle errors gracefully**: Return error info instead of raising exceptions
5. **Session isolation**: Use ``get_session_id()`` for multi-user scenarios
6. **Async by default**: Prefer async tools for I/O operations
7. **Security**: Validate inputs, especially for file/shell operations
8. **Testing**: Test tools independently before using with agents

Security Considerations
-----------------------

.. warning::

   Toolsets can execute arbitrary code. Always:

   - Validate and sanitize inputs
   - Run in sandboxed environments for shell/code tools
   - Limit file access to specific directories
   - Avoid exposing sensitive operations to untrusted input
   - Log tool invocations for auditing
