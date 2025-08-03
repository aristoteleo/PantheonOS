ChatRoom Module
===============

.. module:: pantheon.chatroom

The chatroom module provides a service for hosting agent conversations with web UI support.

Main Components
---------------

ChatRoom Factory
~~~~~~~~~~~~~~~~

.. automodule:: pantheon.chatroom.factory
   :members:
   :undoc-members:

ChatRoom Class
~~~~~~~~~~~~~~

.. automodule:: pantheon.chatroom.room
   :members:
   :undoc-members:

Thread Management
~~~~~~~~~~~~~~~~~

.. automodule:: pantheon.chatroom.thread
   :members:
   :undoc-members:

Starting ChatRoom Service
-------------------------

Command Line
~~~~~~~~~~~~

.. code-block:: bash

   # Basic usage
   python -m pantheon.chatroom

   # With configuration file
   python -m pantheon.chatroom --config my_config.yaml

   # With custom port
   python -m pantheon.chatroom --port 8080

Programmatic Usage
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.chatroom import start_chatroom
   from pantheon.agent import Agent

   # Create agents
   agent1 = Agent(name="helper", instructions="Be helpful")
   agent2 = Agent(name="expert", instructions="Provide expertise")

   # Start chatroom service
   service_id = await start_chatroom(
       agents=[agent1, agent2],
       name="My ChatRoom"
   )

   print(f"Connect at https://pantheon-ui.vercel.app with ID: {service_id}")

Configuration Files
-------------------

YAML Configuration
~~~~~~~~~~~~~~~~~~

Create a configuration file to define your chatroom:

.. code-block:: yaml

   # config.yaml
   name: "Customer Support ChatRoom"
   agents:
     - name: "support_agent"
       instructions: "You are a helpful customer support agent"
       model: "gpt-4o-mini"
       icon: "🎧"
     
     - name: "technical_expert"
       instructions: "You handle technical questions"
       model: "gpt-4o-mini"
       icon: "🔧"

   # Optional team configuration
   team:
     type: "swarm"  # or "sequential", "moa", "swarmcenter"

Load the configuration:

.. code-block:: bash

   python -m pantheon.chatroom --config config.yaml

Complex Configuration
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: "Research Assistant"
   
   # Define multiple agents
   agents:
     - name: "researcher"
       instructions: |
         You are an expert researcher.
         - Search for accurate information
         - Cite sources
         - Provide comprehensive answers
       model: "gpt-4"
       tools:
         - "web_search"
         - "document_reader"
     
     - name: "analyst"
       instructions: |
         You analyze research findings.
         - Identify patterns
         - Draw conclusions
         - Create summaries
       model: "gpt-4o-mini"
     
     - name: "writer"
       instructions: |
         You create well-written content.
         - Clear and engaging writing
         - Proper structure
         - Grammar and style
       model: "gpt-4o-mini"

   # Team configuration
   team:
     type: "sequential"
     name: "Research Pipeline"

API Integration
---------------

The ChatRoom service exposes a WebSocket API for real-time communication.

Connection Flow
~~~~~~~~~~~~~~~

1. Start the chatroom service
2. Get the service ID
3. Connect via WebSocket at the UI
4. Send/receive messages

Message Format
~~~~~~~~~~~~~~

Messages follow this structure:

.. code-block:: json

   {
     "type": "message",
     "content": "User message here",
     "agent": "agent_name",  // Optional: specific agent
     "thread_id": "thread_123"
   }

Response format:

.. code-block:: json

   {
     "type": "response",
     "content": "Agent response",
     "agent": "agent_name",
     "thread_id": "thread_123",
     "metadata": {
       "model": "gpt-4",
       "tokens": 150
     }
   }

Advanced Features
-----------------

Multi-Threading
~~~~~~~~~~~~~~~

ChatRoom supports multiple conversation threads:

.. code-block:: python

   from pantheon.chatroom import ChatRoom

   chatroom = ChatRoom(agents=[agent1, agent2])

   # Each thread maintains separate context
   thread1 = chatroom.create_thread("user1")
   thread2 = chatroom.create_thread("user2")

Session Management
~~~~~~~~~~~~~~~~~~

Sessions are automatically managed with:

- Unique thread IDs
- Conversation history
- Agent state preservation
- Automatic cleanup

Custom ChatRoom
~~~~~~~~~~~~~~~

Create a custom chatroom implementation:

.. code-block:: python

   from pantheon.chatroom.room import ChatRoom
   from pantheon.chatroom.factory import ChatRoomFactory

   class CustomChatRoom(ChatRoom):
       async def on_message(self, message, thread_id):
           # Custom message handling
           result = await super().on_message(message, thread_id)
           
           # Additional processing
           await self.log_interaction(message, result)
           
           return result
       
       async def log_interaction(self, message, result):
           # Custom logging logic
           pass

   # Register custom chatroom
   factory = ChatRoomFactory()
   factory.register("custom", CustomChatRoom)

Integration Examples
--------------------

Web Application
~~~~~~~~~~~~~~~

.. code-block:: python

   from fastapi import FastAPI
   from pantheon.chatroom import start_chatroom

   app = FastAPI()

   @app.on_event("startup")
   async def startup():
       # Start chatroom on app startup
       service_id = await start_chatroom(
           agents=[...],
           name="Web App ChatRoom"
       )
       app.state.chatroom_id = service_id

   @app.get("/chatroom-id")
   async def get_chatroom_id():
       return {"service_id": app.state.chatroom_id}

Slack Integration
~~~~~~~~~~~~~~~~~

The chatroom module includes Slack integration:

.. code-block:: python

   from pantheon.slack import SlackApp
   from pantheon.agent import Agent

   # Create Slack app with agents
   slack_app = SlackApp(
       agents=[
           Agent(name="slack_bot", instructions="Help Slack users")
       ]
   )

   # Run Slack app
   await slack_app.start()

Best Practices
--------------

1. **Agent Configuration**: Define clear agent roles and instructions
2. **Resource Management**: Set appropriate timeouts and limits
3. **Error Handling**: Implement proper error responses
4. **Security**: Use authentication for production deployments
5. **Monitoring**: Track usage and performance metrics

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Service won't start:**

- Check if port is already in use
- Verify API keys are set
- Check agent configuration

**Connection failures:**

- Ensure service ID is correct
- Check network connectivity
- Verify WebSocket support

**Performance issues:**

- Use appropriate models for tasks
- Implement caching where possible
- Monitor token usage