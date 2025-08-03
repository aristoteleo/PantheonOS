Agents Guide
============

This guide covers creating and using agents in Pantheon.

Creating an Agent
-----------------

Basic Agent
~~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent

   agent = Agent(
       name="my_agent",
       instructions="You are a helpful assistant.",
       model="gpt-4o-mini"  # Default model
   )

Agent Parameters
~~~~~~~~~~~~~~~~

The ``Agent`` class accepts these parameters:

- ``name`` (str): Agent identifier
- ``instructions`` (str): System prompt defining agent behavior  
- ``model`` (str): LLM model to use (default: "gpt-4.1-mini")
- ``icon`` (str): Display icon (default: '🤖')
- ``tools`` (list): Functions the agent can call
- ``use_memory`` (bool): Enable memory persistence (default: True)
- ``tool_timeout`` (int): Tool execution timeout in seconds (default: 600)

Using Tools
-----------

Adding Existing Tools
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from magique.ai.tools.web_browse.duckduckgo import duckduckgo_search
   from magique.ai.tools.web_browse.web_crawl import web_crawl

   agent = Agent(
       name="researcher",
       instructions="You research topics using web search.",
       tools=[duckduckgo_search, web_crawl]
   )

Creating Custom Tools
~~~~~~~~~~~~~~~~~~~~~

Use the ``@agent.tool`` decorator:

.. code-block:: python

   agent = Agent(name="calculator", instructions="You help with math.")

   @agent.tool
   def add_numbers(a: int, b: int) -> int:
       """Add two numbers together."""
       return a + b

   @agent.tool
   def multiply(a: float, b: float) -> float:
       """Multiply two numbers."""
       return a * b

Remote Toolsets
~~~~~~~~~~~~~~~

For resource-intensive tools, use remote toolsets:

.. code-block:: python

   from magique.ai.tools.python import PythonInterpreterToolSet
   from magique.ai.toolset import run_toolsets

   async def setup_agent():
       toolset = PythonInterpreterToolSet("python_interpreter")
       
       async with run_toolsets([toolset]):
           agent = Agent(
               name="coder",
               instructions="You can run Python code."
           )
           await agent.remote_toolset(toolset.service_id)
           return agent

Interacting with Agents
-----------------------

Chat Interface
~~~~~~~~~~~~~~

The simplest way to interact:

.. code-block:: python

   import asyncio

   async def main():
       agent = Agent(name="assistant", instructions="Be helpful.")
       await agent.chat()  # Interactive REPL

   asyncio.run(main())

Programmatic Execution
~~~~~~~~~~~~~~~~~~~~~~

For programmatic use:

.. code-block:: python

   async def process_task():
       agent = Agent(name="processor", instructions="Process data.")
       
       # Single message
       response = await agent.run("Analyze this data: [1, 2, 3]")
       
       # With context
       response = await agent.run(
           "Continue the analysis",
           context_variables={"previous_result": response}
       )

Memory and Context
------------------

Agents maintain conversation history by default:

.. code-block:: python

   from pantheon.memory import Memory

   # Custom memory configuration
   memory = Memory(agent_id="unique_id")

   agent = Agent(
       name="memory_agent",
       instructions="Remember our conversations.",
       memory=memory
   )

Models and Providers
--------------------

Supported models include:

- OpenAI: ``gpt-4``, ``gpt-4o-mini``, ``gpt-4.1-mini``
- Custom models via ``force_litellm=True``

.. code-block:: python

   # Using specific model
   agent = Agent(
       name="advanced",
       instructions="Complex reasoning tasks.",
       model="gpt-4"
   )

   # Multiple model fallback
   agent = Agent(
       name="resilient",
       instructions="With fallback support.",
       model=["gpt-4", "gpt-4o-mini"]  # Try first, fallback to second
   )

Agent Transfer (Swarm Pattern)
------------------------------

Agents can transfer control to other agents:

.. code-block:: python

   agent1 = Agent(name="Agent1", instructions="First agent")
   agent2 = Agent(name="Agent2", instructions="Second agent")

   @agent1.tool
   def transfer_to_agent2():
       """Transfer control to Agent2."""
       return agent2

This enables dynamic handoffs in SwarmTeam configurations.

Best Practices
--------------

1. **Clear Instructions**: Be specific about the agent's role and capabilities
2. **Appropriate Tools**: Only add tools the agent needs
3. **Model Selection**: Use smaller models for simple tasks, larger for complex reasoning
4. **Error Handling**: Tools should handle errors gracefully
5. **Memory Usage**: Disable memory for stateless operations

Example: Complete Agent
-----------------------

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from datetime import datetime

   async def main():
       agent = Agent(
           name="personal_assistant",
           instructions="""You are a personal assistant that helps with:
           - Scheduling and time management
           - Simple calculations
           - General questions
           Be friendly and professional.""",
           model="gpt-4o-mini",
           icon="🗓️"
       )
       
       @agent.tool
       def get_current_time() -> str:
           """Get the current date and time."""
           return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
       
       @agent.tool
       def set_reminder(task: str, time: str) -> str:
           """Set a reminder for a task."""
           return f"Reminder set: '{task}' at {time}"
       
       # Run the agent
       await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Next Steps
----------

- Learn about :doc:`teams` for multi-agent collaboration
- Explore the :doc:`chatroom` for web-based interfaces
- Check :doc:`../examples/index` for more examples