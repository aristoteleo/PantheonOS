Agent Module
============

.. module:: pantheon.agent

The agent module provides the core :class:`Agent` class for creating AI agents.

Agent Class
-----------

.. autoclass:: pantheon.agent.Agent
   :members:
   :undoc-members:
   :show-inheritance:

   .. automethod:: __init__

Constructor Parameters
~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 20 20 60
   :header-rows: 1

   * - Parameter
     - Type
     - Description
   * - name
     - str
     - Unique name for the agent
   * - instructions
     - str
     - System instructions defining agent behavior
   * - model
     - str | list[str]
     - LLM model(s) to use (default: "gpt-4.1-mini")
   * - icon
     - str
     - Display icon for the agent (default: '🤖')
   * - tools
     - list[Callable]
     - List of tool functions the agent can use
   * - response_format
     - Any
     - Expected response format (for structured outputs)
   * - use_memory
     - bool
     - Enable memory persistence (default: True)
   * - memory
     - Memory
     - Custom memory instance
   * - tool_timeout
     - int
     - Tool execution timeout in seconds (default: 600)
   * - force_litellm
     - bool
     - Force use of litellm backend (default: False)
   * - max_tool_content_length
     - int | None
     - Maximum length for tool outputs (default: 100000)

Methods
-------

.. automethod:: pantheon.agent.Agent.tool
.. automethod:: pantheon.agent.Agent.chat
.. automethod:: pantheon.agent.Agent.run
.. automethod:: pantheon.agent.Agent.remote_toolset
.. automethod:: pantheon.agent.Agent.async_setup

Response Classes
----------------

.. autoclass:: pantheon.agent.AgentResponse
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: pantheon.agent.AgentTransfer
   :members:
   :undoc-members:
   :show-inheritance:

Type Definitions
----------------

.. autodata:: pantheon.agent.AgentInput
   :annotation:

   Type alias for valid agent inputs:
   
   - ``str``: Simple text message
   - ``BaseModel``: Structured input
   - ``AgentResponse``: Response from another agent
   - ``list[str | BaseModel | dict]``: Multiple inputs
   - ``AgentTransfer``: Transfer from another agent
   - ``VisionInput``: Image input

Examples
--------

Basic Agent
~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent

   agent = Agent(
       name="assistant",
       instructions="You are a helpful AI assistant.",
       model="gpt-4o-mini"
   )

   # Interactive chat
   await agent.chat()

Agent with Tools
~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent

   agent = Agent(
       name="calculator",
       instructions="You help with calculations."
   )

   @agent.tool
   def add(a: int, b: int) -> int:
       """Add two numbers."""
       return a + b

   @agent.tool
   def multiply(a: float, b: float) -> float:
       """Multiply two numbers."""
       return a * b

   # Run with tools
   result = await agent.run("What is 5 + 3?")
   print(result.content)

Agent with Memory
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.memory import Memory

   # Custom memory
   memory = Memory(agent_id="unique_id")

   agent = Agent(
       name="memory_bot",
       instructions="Remember our conversations.",
       memory=memory
   )

   # First interaction
   await agent.run("My name is Alice")
   
   # Later interaction (remembers context)
   result = await agent.run("What's my name?")
   print(result.content)  # Should mention "Alice"

Multi-Model Fallback
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   agent = Agent(
       name="robust_agent",
       instructions="Handle tasks reliably.",
       model=["gpt-4", "gpt-4o-mini"]  # Try GPT-4 first, fallback to mini
   )

Remote Toolset
~~~~~~~~~~~~~~

.. code-block:: python

   from magique.ai.tools.python import PythonInterpreterToolSet
   from magique.ai.toolset import run_toolsets

   async def setup():
       toolset = PythonInterpreterToolSet("python")
       
       async with run_toolsets([toolset]):
           agent = Agent(
               name="coder",
               instructions="You can run Python code."
           )
           await agent.remote_toolset(toolset.service_id)
           
           result = await agent.run("Calculate fibonacci(10)")
           print(result.content)