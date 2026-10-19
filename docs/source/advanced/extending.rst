Extending Pantheon
==================

Creating custom components and integrations.

Custom Toolsets
---------------

**Basic ToolSet**

.. code-block:: python

   from pantheonos.toolset import ToolSet, tool

   class MyToolSet(ToolSet):
       """Custom toolset for specific operations."""

       def __init__(self, config: dict = None):
           self.config = config or {}

       @tool
       def my_operation(self, param: str) -> str:
           """Perform a custom operation.

           Args:
               param: Input parameter

           Returns:
               Operation result
           """
           return f"Result: {param}"

       @tool
       async def async_operation(self, url: str) -> str:
           """Async operation for I/O.

           Args:
               url: URL to process

           Returns:
               Processed content
           """
           async with aiohttp.ClientSession() as session:
               async with session.get(url) as resp:
                   return await resp.text()

**With State**

.. code-block:: python

   class StatefulToolSet(ToolSet):
       def __init__(self):
           self.cache = {}
           self.call_count = 0

       @tool
       def cached_fetch(self, key: str) -> str:
           """Fetch with caching."""
           self.call_count += 1
           if key in self.cache:
               return self.cache[key]
           result = self._fetch(key)
           self.cache[key] = result
           return result

Custom Providers
----------------

**Basic Provider**

.. code-block:: python

   from pantheon.providers import Provider

   class MyProvider(Provider):
       def __init__(self, api_key: str):
           self.api_key = api_key
           self._client = None

       async def connect(self):
           """Initialize connection."""
           self._client = MyClient(self.api_key)
           await self._client.connect()

       async def disconnect(self):
           """Clean up connection."""
           if self._client:
               await self._client.close()

       def get_tools(self) -> list:
           """Return available tools."""
           return [
               {
                   "name": "custom_search",
                   "description": "Search using custom API",
                   "parameters": {
                       "type": "object",
                       "properties": {
                           "query": {"type": "string"}
                       },
                       "required": ["query"]
                   }
               }
           ]

       async def execute_tool(self, name: str, arguments: dict) -> str:
           """Execute a tool."""
           if name == "custom_search":
               return await self._client.search(arguments["query"])
           raise ValueError(f"Unknown tool: {name}")

Custom Team Types
-----------------

**Custom Orchestration**

.. code-block:: python

   from pantheon.team import Team

   class PriorityTeam(Team):
       """Team that routes to agents based on priority."""

       def __init__(self, agents: list, priorities: dict):
           super().__init__(agents=agents)
           self.priorities = priorities

       async def run(self, message: str) -> str:
           # Analyze message to determine priority
           priority = self._analyze_priority(message)

           # Route to appropriate agent
           agent_name = self.priorities.get(priority, "default")
           agent = self.get_agent(agent_name)

           return await agent.run(message)

       def _analyze_priority(self, message: str) -> str:
           if "urgent" in message.lower():
               return "high"
           return "normal"

Custom Memory Backends
----------------------

**Database-Backed Memory**

.. code-block:: python

   from pantheon.memory import Memory
   import sqlite3

   class SQLiteMemory(Memory):
       def __init__(self, db_path: str):
           self.db_path = db_path
           self._init_db()

       def _init_db(self):
           conn = sqlite3.connect(self.db_path)
           conn.execute("""
               CREATE TABLE IF NOT EXISTS messages (
                   id INTEGER PRIMARY KEY,
                   role TEXT,
                   content TEXT,
                   timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
               )
           """)
           conn.commit()
           conn.close()

       def append(self, message: dict):
           conn = sqlite3.connect(self.db_path)
           conn.execute(
               "INSERT INTO messages (role, content) VALUES (?, ?)",
               (message["role"], message["content"])
           )
           conn.commit()
           conn.close()

       @property
       def messages(self) -> list:
           conn = sqlite3.connect(self.db_path)
           cursor = conn.execute(
               "SELECT role, content FROM messages ORDER BY id"
           )
           messages = [
               {"role": row[0], "content": row[1]}
               for row in cursor.fetchall()
           ]
           conn.close()
           return messages

Custom Evaluators
-----------------

**For Evolution System**

.. code-block:: python

   from pantheon.evolution import Evaluator

   class DomainSpecificEvaluator(Evaluator):
       def __init__(self, criteria: list):
           self.criteria = criteria

       async def evaluate(
           self,
           response: str,
           expected: str,
           context: dict
       ) -> float:
           score = 0.0
           weights = 1.0 / len(self.criteria)

           for criterion in self.criteria:
               if criterion == "accuracy":
                   score += weights * self._check_accuracy(response, expected)
               elif criterion == "completeness":
                   score += weights * self._check_completeness(response)
               elif criterion == "format":
                   score += weights * self._check_format(response)

           return score

       def _check_accuracy(self, response: str, expected: str) -> float:
           # Implementation
           pass

Middleware
----------

**Request/Response Middleware**

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.middleware import Middleware

   class LoggingMiddleware(Middleware):
       async def before_run(self, message: str, agent: Agent) -> str:
           print(f"[{agent.name}] Input: {message[:100]}...")
           return message

       async def after_run(self, response: str, agent: Agent) -> str:
           print(f"[{agent.name}] Output: {response[:100]}...")
           return response

   class FilterMiddleware(Middleware):
       def __init__(self, blocked_words: list):
           self.blocked_words = blocked_words

       async def before_run(self, message: str, agent: Agent) -> str:
           for word in self.blocked_words:
               if word in message.lower():
                   raise ValueError(f"Blocked content: {word}")
           return message

   # Use middleware
   agent = Agent(
       name="safe_assistant",
       middleware=[LoggingMiddleware(), FilterMiddleware(["dangerous"])]
   )

Hooks
-----

**Agent Lifecycle Hooks**

.. code-block:: python

   from pantheon.agent import Agent

   agent = Agent(name="assistant", ...)

   @agent.on_start
   async def on_start():
       print("Agent starting...")

   @agent.on_tool_call
   async def on_tool(name: str, args: dict):
       print(f"Calling tool: {name}")

   @agent.on_response
   async def on_response(response):
       print(f"Response generated: {len(response.content)} chars")

   @agent.on_error
   async def on_error(error: Exception):
       print(f"Error: {error}")

Custom Commands
---------------

**REPL Commands**

.. code-block:: python

   from pantheon.repl import REPLCommand

   class MyCommand(REPLCommand):
       name = "mycommand"
       description = "My custom command"

       async def execute(self, args: str, context: dict):
           # Implementation
           return f"Executed with args: {args}"

   # Register
   from pantheon.repl import register_command
   register_command(MyCommand())

Plugins
-------

**Plugin Structure**

.. code-block:: text

   my_plugin/
   ├── __init__.py
   ├── toolsets.py
   ├── providers.py
   └── commands.py

**Plugin Registration**

.. code-block:: python

   # my_plugin/__init__.py
   from pantheon.plugins import Plugin

   class MyPlugin(Plugin):
       name = "my_plugin"
       version = "1.0.0"

       def register(self, app):
           # Register toolsets
           from .toolsets import MyToolSet
           app.register_toolset("my_tools", MyToolSet)

           # Register commands
           from .commands import MyCommand
           app.register_command(MyCommand())

Testing Extensions
------------------

**Testing Toolsets**

.. code-block:: python

   import pytest
   from my_plugin.toolsets import MyToolSet

   @pytest.fixture
   def toolset():
       return MyToolSet(config={"test": True})

   @pytest.mark.asyncio
   async def test_my_operation(toolset):
       result = toolset.my_operation("test")
       assert "Result:" in result

   @pytest.mark.asyncio
   async def test_async_operation(toolset):
       result = await toolset.async_operation("http://example.com")
       assert len(result) > 0

Best Practices
--------------

1. **Follow Patterns**: Match existing Pantheon patterns
2. **Document Well**: Provide clear docstrings and examples
3. **Handle Errors**: Gracefully handle and report errors
4. **Test Thoroughly**: Write tests for all extensions
5. **Type Hints**: Use type hints for better IDE support
6. **Async When Needed**: Use async for I/O operations
