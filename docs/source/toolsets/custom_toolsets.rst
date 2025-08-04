Custom Toolsets
===============

Creating custom toolsets allows you to extend Pantheon agents with domain-specific capabilities tailored to your unique requirements. This guide covers how to design, implement, and deploy custom toolsets.

Overview
--------

Custom toolsets enable you to:

- Integrate proprietary APIs and services
- Add domain-specific functionality
- Create specialized computational tools
- Build reusable tool collections
- Implement security-controlled operations

Creating Custom Tools
---------------------

Simple Function Tools
~~~~~~~~~~~~~~~~~~~~~

The simplest way to create a custom tool is by decorating a Python function::

    from pantheon.agent import Agent
    
    agent = Agent(name="assistant", instructions="...")
    
    @agent.tool
    def custom_calculation(x: float, y: float) -> float:
        """Perform a custom calculation on two numbers."""
        return x ** y + (x * y)

The function's docstring and type hints are automatically used to help the agent understand when and how to use the tool.

Async Tools
~~~~~~~~~~~

For I/O-bound operations, use async functions::

    @agent.tool
    async def fetch_api_data(endpoint: str) -> dict:
        """Fetch data from our internal API."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.internal.com/{endpoint}") as response:
                return await response.json()

Creating Toolset Classes
------------------------

For more complex tool collections, create a toolset class::

    from pantheon.toolsets.base import BaseToolSet
    
    class DataAnalysisToolSet(BaseToolSet):
        def __init__(self, name: str):
            super().__init__(name)
            self.setup_tools()
        
        def setup_tools(self):
            @self.tool
            def analyze_timeseries(data: list[float]) -> dict:
                """Analyze time series data for trends and patterns."""
                # Implementation here
                pass
            
            @self.tool
            async def fetch_market_data(symbol: str) -> dict:
                """Fetch latest market data for a symbol."""
                # Implementation here
                pass

Best Practices
--------------

1. **Clear Documentation**: Always provide detailed docstrings
2. **Type Hints**: Use type annotations for all parameters and returns
3. **Error Handling**: Implement robust error handling
4. **Security**: Validate inputs and sanitize outputs
5. **Performance**: Consider async for I/O operations
6. **Testing**: Write comprehensive tests for your tools

Deployment
----------

Deploy custom toolsets as services for distributed access::

    from pantheon.toolsets.utils.toolset import run_toolset_service
    
    toolset = DataAnalysisToolSet("analysis_tools")
    await run_toolset_service(toolset, host="0.0.0.0", port=8001)