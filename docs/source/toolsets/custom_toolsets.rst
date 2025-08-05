Custom Toolsets
===============

Creating custom toolsets allows you to extend Pantheon agents with domain-specific capabilities. All toolsets inherit from the ``ToolSet`` base class and use the ``@tool`` decorator to expose methods as tools.

Overview
--------

The toolset framework provides:

- **Automatic Tool Registration**: Methods with ``@tool`` decorator are automatically exposed
- **Service Infrastructure**: Built-in support for running as distributed services
- **Flexible Execution**: Tools can run as local, thread, or process jobs
- **Lifecycle Management**: Setup and cleanup hooks for resource management
- **MCP Compatibility**: Optional Model Context Protocol server support

Creating a Custom Toolset
-------------------------

Basic Structure
~~~~~~~~~~~~~~~

All custom toolsets inherit from ``ToolSet``::

    from pantheon.toolsets.utils.toolset import ToolSet, tool
    
    class MyCustomToolSet(ToolSet):
        def __init__(self, name: str = "my_tools", **kwargs):
            super().__init__(name, **kwargs)
            # Initialize any resources
        
        @tool
        async def my_tool(self, param: str) -> str:
            """Tool description for the agent."""
            return f"Processed: {param}"
        
        async def run_setup(self):
            """Optional: Setup before the toolset starts."""
            # Initialize connections, load models, etc.
            pass

Tool Decorator Options
~~~~~~~~~~~~~~~~~~~~~~

The ``@tool`` decorator supports execution parameters::

    class DataToolSet(ToolSet):
        @tool(job_type="local")  # Runs in the same process
        async def quick_calc(self, x: int) -> int:
            return x * 2
        
        @tool(job_type="thread")  # Runs in a thread pool
        async def io_operation(self, url: str) -> str:
            # Good for I/O-bound operations
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    return await resp.text()
        
        @tool(job_type="process")  # Runs in a separate process
        async def heavy_computation(self, data: list) -> dict:
            # Good for CPU-bound operations
            result = expensive_computation(data)
            return result

Resource Management
~~~~~~~~~~~~~~~~~~~

Toolsets can manage resources with lifecycle hooks::

    class DatabaseToolSet(ToolSet):
        def __init__(self, db_url: str, **kwargs):
            super().__init__("db_tools", **kwargs)
            self.db_url = db_url
            self.connection = None
        
        async def run_setup(self):
            """Called before the toolset starts."""
            self.connection = await asyncpg.connect(self.db_url)
        
        @tool
        async def query(self, sql: str) -> list[dict]:
            """Execute a SQL query."""
            rows = await self.connection.fetch(sql)
            return [dict(row) for row in rows]
        
        # Cleanup happens automatically when the service stops

Running Toolsets
----------------

As a Standalone Service
~~~~~~~~~~~~~~~~~~~~~~~

Run your toolset as an independent service::

    # In your toolset file
    if __name__ == "__main__":
        from pantheon.toolsets.utils.toolset import toolset_cli
        toolset_cli(MyCustomToolSet, "my_custom_tools")

Then from command line::

    # Run as Magique service
    python -m my_toolset --service-name my_tools
    
    # Run as MCP server
    python -m my_toolset --mcp

With Context Manager
~~~~~~~~~~~~~~~~~~~~

For testing or temporary use::

    from pantheon.toolsets.utils.toolset import run_toolsets
    
    async with run_toolsets([MyCustomToolSet("tools")]):
        # Toolsets are running
        agent = Agent(name="assistant")
        await agent.remote_toolset(toolset.service_id)
        # Use the agent

Connecting from Agents
----------------------

Agents connect to toolsets using their service ID::

    # Method 1: Direct connection
    agent = Agent(name="assistant")
    await agent.remote_toolset("your-service-id")
    
    # Method 2: Auto-discovery by name
    await agent.remote_toolset(service_name="my_tools")

Best Practices
--------------

1. **Type Annotations**: Always use type hints for parameters and returns
2. **Docstrings**: Provide clear descriptions for agent understanding
3. **Error Handling**: Return informative error messages
4. **Security**: Validate inputs, especially for system operations
5. **Stateless Design**: Prefer stateless tools when possible
6. **Resource Cleanup**: Use lifecycle hooks for proper resource management

Common Patterns
---------------

Stateful Tools
~~~~~~~~~~~~~~

For tools that need to maintain state::

    class SessionToolSet(ToolSet):
        def __init__(self, **kwargs):
            super().__init__("session_tools", **kwargs)
            self.sessions = {}
        
        @tool
        async def create_session(self, user_id: str) -> str:
            """Create a new session for a user."""
            session_id = str(uuid.uuid4())
            self.sessions[session_id] = {"user": user_id, "data": {}}
            return session_id
        
        @tool
        async def update_session(self, session_id: str, key: str, value: any) -> bool:
            """Update session data."""
            if session_id in self.sessions:
                self.sessions[session_id]["data"][key] = value
                return True
            return False

Tool Composition
~~~~~~~~~~~~~~~~

Combine multiple capabilities::

    class CompositeToolSet(ToolSet):
        def __init__(self, **kwargs):
            super().__init__("composite_tools", **kwargs)
            self.text_processor = TextProcessor()
            self.data_analyzer = DataAnalyzer()
        
        @tool
        async def analyze_document(self, doc_path: str) -> dict:
            """Extract and analyze document content."""
            # Extract text
            text = await self.text_processor.extract(doc_path)
            
            # Analyze content
            analysis = await self.data_analyzer.analyze(text)
            
            return {
                "word_count": len(text.split()),
                "analysis": analysis
            }