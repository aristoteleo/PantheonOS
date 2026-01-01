Python API
==========

Full programmatic control over Pantheon agents and teams.

Overview
--------

The Python API provides complete control over:

- Agent creation and configuration
- Team orchestration
- Toolset integration
- Memory management
- Model selection

Quick Example
-------------

.. code-block:: python

   import asyncio
   from pantheon import Agent

   async def main():
       agent = Agent(
           name="assistant",
           instructions="You are a helpful assistant.",
           model="gpt-4o-mini"
       )

       # Single query
       response = await agent.run("What is 2 + 2?")
       print(response.content)

       # Interactive chat
       await agent.chat()

   asyncio.run(main())

Core Classes
------------

**Agent**

The fundamental building block. Represents an AI-powered entity.

.. code-block:: python

   from pantheon import Agent

   agent = Agent(
       name="assistant",
       instructions="...",
       model="gpt-4o-mini",
       tools=[...]
   )

See :doc:`agent` for details.

**Team**

Multiple agents working together.

.. code-block:: python

   from pantheon.team import PantheonTeam

   team = PantheonTeam([agent1, agent2, agent3])

See :doc:`team` for team patterns.

**ToolSet**

Extend agent capabilities with tools.

.. code-block:: python

   from pantheon.toolsets import FileManagerToolSet

   agent = Agent(
       name="dev",
       tools=[FileManagerToolSet()]
   )

See :doc:`toolsets` for available toolsets.

Async Pattern
-------------

All Pantheon APIs are async:

.. code-block:: python

   import asyncio

   async def main():
       # Your Pantheon code here
       pass

   asyncio.run(main())

For Jupyter notebooks:

.. code-block:: python

   # In Jupyter, you can use await directly
   response = await agent.run("Hello!")

Integration
-----------

**With FastAPI**

.. code-block:: python

   from fastapi import FastAPI
   from pantheon import Agent

   app = FastAPI()
   agent = Agent(name="api_assistant", ...)

   @app.post("/chat")
   async def chat(message: str):
       response = await agent.run(message)
       return {"response": response.content}

**With LangChain**

Pantheon can work alongside LangChain:

.. code-block:: python

   # Use Pantheon agents for specific tasks
   # Use LangChain for chains/pipelines

Next Steps
----------

- :doc:`agent` - Agent API reference
- :doc:`team` - Team patterns
- :doc:`toolsets` - Available toolsets
- :doc:`advanced` - Advanced patterns

.. toctree::
   :hidden:
   :maxdepth: 1

   quickstart
   agent
   team
   toolsets
   advanced
