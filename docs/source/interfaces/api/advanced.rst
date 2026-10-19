Advanced API Usage
==================

Advanced patterns and techniques for the Pantheon Python API.

Execution Context
-----------------

Tools can access the execution context for advanced operations:

.. code-block:: python

   from pantheonos.toolset import tool, ExecutionContext

   @tool
   async def complex_task(
       self,
       query: str,
       context_variables: ExecutionContext
   ) -> str:
       """Perform a complex task."""
       # Call the agent during tool execution
       result = await context_variables.call_agent(
           messages=[{"role": "user", "content": query}],
           model="gpt-4o-mini"
       )
       return result.content

Providers
---------

Providers abstract different tool sources:

**MCP Provider**

.. code-block:: python

   from pantheon.providers import MCPProvider

   mcp = MCPProvider("npx -y @anthropic/mcp-server-filesystem")

   agent = Agent(
       name="assistant",
       instructions="You are a helpful assistant."
   )
   await agent.mcp(name="mcp_tools", provider=mcp)

**Local Provider**

.. code-block:: python

   from pantheon.providers import LocalProvider
   from pantheon.toolsets import FileManagerToolSet

   provider = LocalProvider(FileManagerToolSet("files"))

**Tool Filtering**

.. code-block:: python

   # Only expose tools with specific prefix
   mcp = MCPProvider("...", filter_prefix="fs_")

Smart Functions
---------------

Create LLM-powered functions:

.. code-block:: python

   from pantheon.smart_func import smart_func

   @smart_func(model="gpt-4o-mini")
   def analyze_sentiment(text: str) -> dict:
       """Analyze text sentiment.

       Returns dict with 'sentiment' and 'confidence'.
       """
       pass  # LLM handles implementation

   result = await analyze_sentiment("I love this!")
   # {'sentiment': 'positive', 'confidence': 0.95}

Memory Management
-----------------

**Custom Memory**

.. code-block:: python

   from pantheon.memory import Memory

   memory = Memory()
   memory.append({"role": "user", "content": "Hello"})
   memory.append({"role": "assistant", "content": "Hi!"})

   agent = Agent(memory=memory)

**Persistence**

.. code-block:: python

   # Save
   memory.save("./chat_history.json")

   # Load
   memory = Memory.load("./chat_history.json")

**Compression**

For long conversations:

.. code-block:: python

   from pantheon.internal.compression import compress_memory

   compressed = await compress_memory(memory, model="gpt-4o-mini")

Model Selection
---------------

**Fallback Chains**

Configure in settings.json:

.. code-block:: json

   {
     "models": {
       "default": "gpt-4o",
       "fallback": ["gpt-4o-mini", "claude-3-sonnet"]
     }
   }

**Per-Agent Override**

.. code-block:: python

   agent = Agent(model="claude-3-opus")  # Ignores default

Streaming
---------

**Token by Token**

.. code-block:: python

   async for chunk in agent.stream("Tell me a story"):
       print(chunk.content, end="", flush=True)
       if chunk.tool_calls:
           print(f"\n[Tool: {chunk.tool_calls}]")

**With Callback**

.. code-block:: python

   async def on_token(chunk):
       print(chunk.content, end="")

   response = await agent.run("...", on_token=on_token)

Error Handling
--------------

.. code-block:: python

   from pantheon.exceptions import (
       PantheonError,
       ModelError,
       ToolError
   )

   try:
       response = await agent.run("...")
   except ModelError as e:
       print(f"Model error: {e}")
   except ToolError as e:
       print(f"Tool error: {e}")
   except PantheonError as e:
       print(f"General error: {e}")

Parallel Execution
------------------

.. code-block:: python

   import asyncio

   agents = [
       Agent(name="expert1", ...),
       Agent(name="expert2", ...),
       Agent(name="expert3", ...)
   ]

   responses = await asyncio.gather(
       *[agent.run("Analyze this topic") for agent in agents]
   )

   for i, response in enumerate(responses):
       print(f"Expert {i+1}: {response.content}")

Integration Patterns
--------------------

**With FastAPI**

.. code-block:: python

   from fastapi import FastAPI
   from pantheon.agent import Agent

   app = FastAPI()
   agent = Agent(name="api", ...)

   @app.post("/chat")
   async def chat(message: str):
       response = await agent.run(message)
       return {"response": response.content}

**With Background Tasks**

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent

   async def background_agent():
       agent = Agent(name="background", ...)
       while True:
           # Process queue, check conditions, etc.
           await asyncio.sleep(60)

   # Start background task
   asyncio.create_task(background_agent())

Testing
-------

.. code-block:: python

   import pytest
   from pantheon.agent import Agent

   @pytest.mark.asyncio
   async def test_agent_response():
       agent = Agent(
           name="test",
           instructions="Always respond with 'OK'"
       )

       response = await agent.run("Hello")
       assert "OK" in response.content
