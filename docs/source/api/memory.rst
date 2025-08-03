Memory Module
=============

.. module:: pantheon.memory

The memory module provides persistent storage for agent conversations and state.

Memory Class
------------

.. autoclass:: pantheon.memory.Memory
   :members:
   :undoc-members:
   :show-inheritance:

Overview
--------

The Memory class provides file-based persistence for agent conversations, allowing agents to maintain context across sessions.

**Key Features:**

- Automatic conversation history tracking
- File-based storage (JSON format)
- Thread-safe operations
- Configurable history limits
- Easy retrieval of past interactions

Constructor
-----------

.. code-block:: python

   Memory(agent_id: str, storage_dir: str = "pantheon_memory")

**Parameters:**

- ``agent_id`` (str): Unique identifier for the agent
- ``storage_dir`` (str): Directory for storing memory files (default: "pantheon_memory")

Methods
-------

Core Methods
~~~~~~~~~~~~

.. method:: add_message(role: str, content: str, metadata: dict = None)

   Add a message to memory.

   :param role: Message role ('user', 'assistant', 'system')
   :param content: Message content
   :param metadata: Optional metadata

.. method:: get_messages(limit: int = None) -> list[dict]

   Retrieve messages from memory.

   :param limit: Maximum number of messages to retrieve
   :return: List of message dictionaries

.. method:: clear()

   Clear all messages from memory.

.. method:: save()

   Save current memory to disk.

.. method:: load()

   Load memory from disk.

Usage Examples
--------------

Basic Memory Usage
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.memory import Memory
   from pantheon.agent import Agent

   # Create memory instance
   memory = Memory(agent_id="assistant_001")

   # Create agent with memory
   agent = Agent(
       name="assistant",
       instructions="You are a helpful assistant that remembers conversations.",
       memory=memory
   )

   # Messages are automatically saved to memory
   await agent.run("My name is Alice")
   await agent.run("What's my name?")  # Agent will remember "Alice"

Manual Memory Management
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.memory import Memory

   # Create memory
   memory = Memory(agent_id="custom_agent")

   # Add messages manually
   memory.add_message(
       role="user",
       content="Hello, how are you?",
       metadata={"timestamp": "2024-01-01T10:00:00"}
   )

   memory.add_message(
       role="assistant",
       content="I'm doing well, thank you!",
       metadata={"model": "gpt-4"}
   )

   # Save to disk
   memory.save()

   # Load from disk later
   memory2 = Memory(agent_id="custom_agent")
   memory2.load()

   # Retrieve messages
   messages = memory2.get_messages(limit=10)
   for msg in messages:
       print(f"{msg['role']}: {msg['content']}")

Memory with Teams
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.memory import Memory
   from pantheon.agent import Agent
   from pantheon.team import SequentialTeam

   # Each agent can have its own memory
   researcher_memory = Memory(agent_id="researcher")
   writer_memory = Memory(agent_id="writer")

   researcher = Agent(
       name="researcher",
       instructions="Research topics",
       memory=researcher_memory
   )

   writer = Agent(
       name="writer",
       instructions="Write summaries",
       memory=writer_memory
   )

   team = SequentialTeam([researcher, writer])

Shared Memory Pattern
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Multiple agents can share the same memory
   shared_memory = Memory(agent_id="team_memory")

   agent1 = Agent(
       name="agent1",
       instructions="First team member",
       memory=shared_memory
   )

   agent2 = Agent(
       name="agent2", 
       instructions="Second team member",
       memory=shared_memory
   )

   # Both agents will have access to the same conversation history

Memory Persistence
------------------

Memory files are stored in JSON format at:

.. code-block:: text

   {storage_dir}/{agent_id}.json

Example file structure:

.. code-block:: json

   {
     "messages": [
       {
         "role": "user",
         "content": "Hello",
         "timestamp": "2024-01-01T10:00:00",
         "metadata": {}
       },
       {
         "role": "assistant",
         "content": "Hello! How can I help you?",
         "timestamp": "2024-01-01T10:00:01",
         "metadata": {"model": "gpt-4"}
       }
     ]
   }

Best Practices
--------------

1. **Unique Agent IDs**: Use unique IDs to avoid memory conflicts
2. **Regular Saves**: Memory auto-saves, but manual saves ensure persistence
3. **Memory Limits**: Set reasonable limits to prevent excessive file sizes
4. **Cleanup**: Periodically clean old memories if not needed
5. **Backup**: Backup memory files for important agents

Advanced Configuration
----------------------

Custom Storage Directory
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import os

   # Use custom directory
   memory = Memory(
       agent_id="production_agent",
       storage_dir=os.path.expanduser("~/.pantheon/memories")
   )

Memory Filtering
~~~~~~~~~~~~~~~~

.. code-block:: python

   # Get recent messages only
   recent_messages = memory.get_messages(limit=50)

   # Filter by role
   user_messages = [
       msg for msg in memory.get_messages()
       if msg['role'] == 'user'
   ]

   # Filter by metadata
   important_messages = [
       msg for msg in memory.get_messages()
       if msg.get('metadata', {}).get('important', False)
   ]