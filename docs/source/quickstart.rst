Quick Start
===========

This guide will help you create your first Pantheon agents and teams.

Prerequisites
-------------

Before running the examples, make sure to set up your API key as an environment variable. For OpenAI models (default):

.. code-block:: bash

   export OPENAI_API_KEY="your-api-key-here"

For other LLM providers, see the model configuration details in the :ref:`Model Selection and Providers <model-selection-providers>` section.

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
           model="gpt-4.1-mini"
       )
       
       # Chat interactively
       await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Adding Tools to Agents
----------------------

Agents become more powerful with tools. Here's a simple example:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   import random

   async def main():
       # Create an agent
       agent = Agent(
           name="weather_assistant",
           instructions="You are a helpful weather assistant.",
           model="gpt-4.1-mini"
       )
       
       # Define a custom tool
       @agent.tool
       def get_weather(city: str) -> str:
           """Get the current weather for a city."""
           # In a real application, this would call a weather API
           temp = random.randint(60, 85)
           conditions = random.choice(["sunny", "cloudy", "rainy"])
           return f"The weather in {city} is {temp}°F and {conditions}."
       
       # Define another tool
       @agent.tool
       def get_forecast(city: str, days: int = 3) -> str:
           """Get weather forecast for a city."""
           return f"The {days}-day forecast for {city} shows mixed conditions."
       
       # Chat with the agent - it can now use these tools
       await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Adding Remote Toolsets
----------------------

Agents can use powerful remote toolsets for specialized tasks like code execution:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from pantheon.toolsets.python import PythonInterpreterToolSet
   from pantheon.toolsets.utils.toolset import run_toolsets

   async def main():
       # Create a Python interpreter toolset
       toolset = PythonInterpreterToolSet("python_interpreter")
       
       # Start the toolset service
       async with run_toolsets([toolset], log_level="WARNING"):
           # Create an agent
           agent = Agent(
               name="data_analyst",
               instructions="You are a data analyst who can run Python code to analyze data.",
               model="gpt-4.1-mini"
           )
           
           # Connect the agent to the remote toolset
           await agent.remote_toolset(toolset.service_id)
           
           # Now the agent can execute Python code
           await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

**Running Toolsets on Remote Machines:**

The toolset doesn't have to run in the same process or even on the same machine. You can start a toolset on a remote computer:

.. code-block:: bash

   # On remote machine (e.g., a GPU server):
   python -m pantheon.toolsets.python --service-name "gpu-python-env"
   # Output: Service ID: abc123-def456-...

Then connect from your local machine:

.. code-block:: python

   # On local machine:
   agent = Agent(
       name="ml_engineer",
       instructions="You are an ML engineer who can run Python code on GPU servers.",
       model="gpt-4.1"
   )
   
   # Connect using the service ID from the remote machine
   await agent.remote_toolset("abc123-def456-...")
   
   # The agent can now execute code on the remote GPU server
   response = await agent.run("Train a simple neural network using PyTorch")

Creating Agent Teams
--------------------

Multiple agents can work together as a team. Here's an example using Swarm Team where agents can dynamically transfer control to each other:

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from pantheon.team import SwarmTeam

   async def main():
       # Create specialized agents
       researcher = Agent(
           name="Researcher",
           instructions="You are an expert researcher. You gather and analyze information.",
           model="gpt-4.1-mini"
       )
       
       writer = Agent(
           name="Writer",
           instructions="You are a professional writer. You create clear, engaging content.",
           model="gpt-4.1-mini"
       )
       
       # Add transfer functions so agents can hand off tasks
       @researcher.tool
       def transfer_to_writer():
           """Transfer control to the writer when research is complete."""
           return writer
       
       @writer.tool
       def transfer_to_researcher():
           """Transfer control to the researcher when more information is needed."""
           return researcher
       
       # Create a swarm team
       team = SwarmTeam([researcher, writer])
       
       # Start chatting - agents will collaborate automatically
       await team.chat()

   if __name__ == "__main__":
       asyncio.run(main())

For more team types (Sequential, SwarmCenter, MoA), see the :doc:`team/index` documentation.

Creating and Configuring ChatRoom
----------------------------------

The ChatRoom service provides a web interface for interacting with your agents:

Basic ChatRoom Setup
~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Start the chatroom with default configuration
   export OPENAI_API_KEY=your_key
   python -m pantheon.chatroom

Then:

1. Copy the service ID from the output
2. Go to https://pantheon-ui.vercel.app/
3. Paste the service ID and click "Connect"
4. Start chatting with your agents!

ChatRoom with Configuration File
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a YAML configuration file to define your ChatRoom:

.. code-block:: yaml

   # chatroom_config.yaml
   triage:
      name: Triage Agent
      instructions: |
        You are the triage agent,
        you should decide which agent to use based on the user's request.
        If no related agent, you can do the task by yourself.
      model: openai/gpt-4.1
      icon: 🤖
    
    data_analysis:
      name: Data Analysis Agent
      instructions: |
        You are a data analysis agent that can analyze data.
        You can use the python_interpreter to analyze the data.
        You can use the file_manager to manage the data and write code.
        Give a task please list your plan with check points first,
        then execute the plan step by step,
        and give the summary in the end.
      model: openai/gpt-4.1
      icon: 📊
      toolsets:
        - python_interpreter
        - file_manager
    
    web_search:
      name: Web search
      instructions: You are a web search agent that can search the web for information.
      model: openai/gpt-4.1
      icon: 🔍
      toolsets:
        - web_browse
    

Then start the ChatRoom with your configuration:

.. code-block:: bash

   python -m pantheon.chatroom --config chatroom_config.yaml

Next Steps
----------

1. **Learn Pantheon's core concepts** - :doc:`concepts`
2. **Understand Pantheon's architecture** - :doc:`architecture`
3. **Deep dive into components (Agent, Team, Toolsets)** - :doc:`agent/index`, :doc:`team/index`, :doc:`toolsets/index`
4. **Explore examples** - :doc:`examples/index`