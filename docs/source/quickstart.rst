Quick Start
===========

This guide will help you create your first Pantheon agents and teams.

Your First Agent
----------------

The simplest way to create an agent:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent

   async def main():
       # Create a basic agent
       agent = Agent(
           name="assistant",
           instructions="You are a helpful assistant.",
           model="gpt-4o-mini"
       )
       
       # Chat interactively
       await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Adding Tools to Agents
----------------------

Agents become more powerful with tools. Here's an example with web search:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from magique.ai.tools.web_browse.duckduckgo import duckduckgo_search
   from magique.ai.tools.web_browse.web_crawl import web_crawl

   async def main():
       # Create an agent with web search capabilities
       search_agent = Agent(
           name="search_expert",
           instructions="You are an expert in search engines. "
                       "You can search the web and crawl websites.",
           model="gpt-4o-mini",
           tools=[duckduckgo_search, web_crawl]
       )
       
       await search_agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Code Execution Agent
--------------------

Create an agent that can run Python code:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from magique.ai.tools.python import PythonInterpreterToolSet
   from magique.ai.toolset import run_toolsets

   async def main():
       toolset = PythonInterpreterToolSet("python_interpreter")
       
       async with run_toolsets([toolset], log_level="WARNING"):
           agent = Agent(
               name="coderun_bot",
               instructions="You are an AI assistant that can run Python code.",
               model="gpt-4o-mini"
           )
           await agent.remote_toolset(toolset.service_id)
           await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Creating Agent Teams
--------------------

Sequential Team
~~~~~~~~~~~~~~~

Agents work one after another:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from pantheon.team import SequentialTeam

   # Create specialized agents
   scifi_fan = Agent(
       name="scifi_fan",
       instructions="You are a scifi fan. You like to read scifi books."
   )

   romance_fan = Agent(
       name="romance_fan", 
       instructions="You are a romance fan. You like to read romance books."
   )

   # Create a sequential team
   team = SequentialTeam([scifi_fan, romance_fan])

   # Run the team
   asyncio.run(team.chat("Recommend me some books."))

Swarm Team
~~~~~~~~~~

Agents can transfer control to each other:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from pantheon.team import SwarmTeam

   async def main():
       # Create agents
       scifi_fan = Agent(
           name="Scifi Fan",
           instructions="You are a scifi fan.",
           model="gpt-4o-mini"
       )
       
       romance_fan = Agent(
           name="Romance Fan",
           instructions="You are a romance fan.",
           model="gpt-4o-mini"
       )
       
       # Add transfer functions
       @scifi_fan.tool
       def transfer_to_romance_fan():
           return romance_fan
       
       @romance_fan.tool
       def transfer_to_scifi_fan():
           return scifi_fan
       
       # Create swarm team
       team = SwarmTeam([scifi_fan, romance_fan])
       await team.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Using the ChatRoom Service
--------------------------

The ChatRoom service provides a web interface:

.. code-block:: bash

   # Start the chatroom
   export OPENAI_API_KEY=your_key
   python -m pantheon.chatroom

Then:

1. Copy the service ID from the output
2. Go to https://pantheon-ui.vercel.app/
3. Paste the service ID and click "Connect"
4. Start chatting with your agents!

Custom Tools
------------

You can create custom tools for agents:

.. code-block:: python

   from pantheon.agent import Agent

   agent = Agent(
       name="CustomAgent",
       instructions="You can use custom tools."
   )

   @agent.tool
   def calculate_sum(a: int, b: int) -> int:
       """Calculate the sum of two numbers."""
       return a + b

   @agent.tool 
   def get_current_time() -> str:
       """Get the current time."""
       from datetime import datetime
       return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

Next Steps
----------

- Explore more :doc:`examples/index`
- Learn about :doc:`guides/teams` patterns
- Understand :doc:`guides/agents` in depth
- Check out available :doc:`guides/tools`