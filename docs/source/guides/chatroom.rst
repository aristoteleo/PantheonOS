ChatRoom Guide
==============

The ChatRoom service provides a web interface for interacting with agents and teams.

Starting the ChatRoom
---------------------

Basic Usage
~~~~~~~~~~~

.. code-block:: bash

   # Set your API key
   export OPENAI_API_KEY=your_openai_api_key
   
   # Start the service
   python -m pantheon.chatroom

The service will output:

.. code-block:: text

   Service started with id: <service-id>
   Copy this ID to connect via the web interface

Connecting via Web UI
~~~~~~~~~~~~~~~~~~~~~

1. Copy the service ID from the terminal
2. Open https://pantheon-ui.vercel.app/
3. Paste the service ID
4. Click "Connect"
5. Start chatting!

Configuration
-------------

The ChatRoom can be configured with a YAML file:

.. code-block:: yaml

   # Example: single_cell_analysis.yaml
   name: "Single Cell Analysis Assistant"
   agents:
     - name: "analyzer"
       instructions: "You help with single cell data analysis"
       model: "gpt-4o-mini"

Load the configuration:

.. code-block:: bash

   python -m pantheon.chatroom --config single_cell_analysis.yaml

Custom ChatRoom
---------------

Create a custom chatroom programmatically:

.. code-block:: python

   from pantheon.chatroom import ChatRoom
   from pantheon.agent import Agent

   # Create agents
   agent1 = Agent(name="helper", instructions="Be helpful")
   agent2 = Agent(name="expert", instructions="Provide expertise")

   # Create chatroom
   chatroom = ChatRoom(
       name="CustomChat",
       agents=[agent1, agent2]
   )

   # Start service
   await chatroom.start()

Features
--------

- **Multi-agent Support**: Chat with individual agents or teams
- **Session Management**: Maintains conversation history
- **Real-time Updates**: Live streaming responses
- **Web-based Interface**: No installation required for users

Integration
-----------

The ChatRoom service can be integrated with:

- Web applications via the service ID
- Slack (in development)
- Other chat platforms (planned)

Next Steps
----------

- Try the :doc:`../examples/index` with ChatRoom
- Learn about :doc:`agents` configuration
- Explore :doc:`teams` for multi-agent setups