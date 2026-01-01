Agent API
=========

The Agent class is the fundamental building block of Pantheon.

.. autoclass:: pantheon.agent.Agent
   :members: run, chat, stream
   :undoc-members:

Creating an Agent
-----------------

**Basic Agent**

.. code-block:: python

   from pantheon import Agent

   agent = Agent(
       name="assistant",
       instructions="You are a helpful assistant.",
       model="gpt-4o-mini"
   )

**With Toolsets**

.. code-block:: python

   from pantheon.toolsets import FileManagerToolSet

   agent = Agent(
       name="developer",
       instructions="You are a developer.",
       model="gpt-4o",
       tools=[FileManagerToolSet()]
   )

**With Memory**

.. code-block:: python

   agent = Agent(
       name="assistant",
       instructions="...",
       use_memory=True
   )

Constructor Parameters
----------------------

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Parameter
     - Type
     - Description
   * - ``name``
     - str
     - Agent name (used for identification)
   * - ``instructions``
     - str
     - System prompt defining agent behavior
   * - ``model``
     - str
     - LLM model to use (e.g., "gpt-4o", "claude-3-opus")
   * - ``tools``
     - list
     - List of ToolSet instances
   * - ``use_memory``
     - bool
     - Enable conversation persistence
   * - ``max_iterations``
     - int
     - Maximum tool call iterations (default: 10)

Methods
-------

run()
~~~~~

Execute a single query and return the response.

.. code-block:: python

   response = await agent.run("What is 2 + 2?")
   print(response.content)

**Parameters:**

- ``message`` (str): The user message

**Returns:** ``AgentResponse``

chat()
~~~~~~

Start an interactive REPL session.

.. code-block:: python

   await agent.chat()

stream()
~~~~~~~~

Stream the response token by token.

.. code-block:: python

   async for chunk in agent.stream("Tell me a story"):
       print(chunk.content, end="")

AgentResponse
-------------

The response object returned by ``run()``:

.. code-block:: python

   response = await agent.run("...")

   # Content
   print(response.content)

   # Messages (full conversation)
   print(response.messages)

   # Tool calls made
   print(response.tool_calls)

   # Token usage
   print(response.usage)

Custom Tools
------------

**Using Decorator**

.. code-block:: python

   @agent.tool
   def my_function(param: str) -> str:
       """Description for the LLM."""
       return f"Result: {param}"

**Using ToolSet**

.. code-block:: python

   from pantheon import ToolSet, tool

   class MyTools(ToolSet):
       @tool
       def calculate(self, expression: str) -> float:
           """Evaluate math expression."""
           return eval(expression)

   agent = Agent(tools=[MyTools()])

Model Selection
---------------

Specify models using provider prefixes:

.. code-block:: python

   # OpenAI
   agent = Agent(model="gpt-4o")
   agent = Agent(model="openai/gpt-4o-mini")

   # Anthropic
   agent = Agent(model="claude-3-opus")
   agent = Agent(model="anthropic/claude-3-sonnet")

   # Other providers (via LiteLLM)
   agent = Agent(model="gemini/gemini-pro")

Best Practices
--------------

**Clear Instructions**

Write specific, clear instructions:

.. code-block:: python

   agent = Agent(
       instructions="""You are a Python developer assistant.

       Your responsibilities:
       - Write clean, well-documented code
       - Follow PEP 8 style guidelines
       - Include error handling

       When asked to write code:
       1. First explain your approach
       2. Write the code
       3. Explain how to use it"""
   )

**Appropriate Model**

- Use ``gpt-4o-mini`` for simple tasks (faster, cheaper)
- Use ``gpt-4o`` or ``claude-3-opus`` for complex tasks

**Tool Selection**

Only include tools the agent needs:

.. code-block:: python

   # Good - specific tools
   agent = Agent(tools=[FileManagerToolSet()])

   # Avoid - too many tools can confuse the model
   agent = Agent(tools=[ToolSet1(), ToolSet2(), ToolSet3(), ...])
