Installation
============

This guide will help you install Pantheon and set up your environment.

Basic Installation
------------------

Install from PyPI
~~~~~~~~~~~~~~~~~

.. code-block:: bash

   pip install pantheon-agents

Install from Source
~~~~~~~~~~~~~~~~~~~

For the latest development version:

.. code-block:: bash

   git clone https://github.com/aristoteleo/pantheon-agents.git
   cd pantheon-agents
   pip install -e .

Environment Setup
-----------------

API Keys
~~~~~~~~

Pantheon requires API keys for LLM providers. Set up at least one:

.. code-block:: bash

   # OpenAI (most commonly used)
   export OPENAI_API_KEY="your-openai-api-key"

Quick Test
----------

Start the ChatRoom Service
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Make sure your API key is set
   export OPENAI_API_KEY=your_openai_api_key
   
   # Start the chatroom
   python -m pantheon.chatroom

You'll see output like:

.. code-block:: text

   Service started with id: <service-id>
   Copy this ID and connect via https://pantheon-ui.vercel.app/

Test with a Simple Agent
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent

   async def main():
       agent = Agent(
           name="test_agent",
           instructions="You are a helpful assistant.",
           model="gpt-4o-mini"  # or "gpt-4.1-mini" 
       )
       await agent.chat()

   if __name__ == "__main__":
       asyncio.run(main())

Dependencies
------------

Core dependencies are automatically installed with the package:

- Python 3.8+
- asyncio support
- Various LLM client libraries

Optional dependencies for specific toolsets:

- **Python code execution**: Installed by default
- **R support**: Requires R installation
- **Web browsing**: Included via magique tools

Next Steps
----------

- :doc:`quickstart` - Create your first agent
- :doc:`examples/index` - Explore example implementations