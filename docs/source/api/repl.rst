REPL Module
===========

.. module:: pantheon.repl

The REPL (Read-Eval-Print Loop) module provides interactive interfaces for agents and teams.

Team REPL
---------

.. autoclass:: pantheon.repl.team.Repl
   :members:
   :undoc-members:
   :show-inheritance:

Single Agent REPL
-----------------

.. automodule:: pantheon.repl.single
   :members:
   :undoc-members:

Overview
--------

The REPL module provides interactive command-line interfaces for:

- Chatting with individual agents
- Interacting with agent teams
- Testing and debugging agent behaviors
- Managing conversation flow

Using Team REPL
---------------

Basic Usage
~~~~~~~~~~~

.. code-block:: python

   from pantheon.repl.team import Repl
   from pantheon.team import SwarmTeam
   from pantheon.agent import Agent

   # Create agents
   agent1 = Agent(name="Agent1", instructions="First agent")
   agent2 = Agent(name="Agent2", instructions="Second agent")

   # Create team
   team = SwarmTeam([agent1, agent2])

   # Start REPL
   repl = Repl(team)
   await repl.run()

With Initial Message
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Start with a specific message
   repl = Repl(team)
   await repl.run("Hello team!")

Custom REPL Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   repl = Repl(
       team,
       prompt=">>> ",  # Custom prompt
       welcome_message="Welcome to the team chat!",
       exit_commands=["quit", "exit", "bye"]
   )

Single Agent REPL
-----------------

The `agent.chat()` method provides a built-in REPL:

.. code-block:: python

   from pantheon.agent import Agent

   agent = Agent(
       name="assistant",
       instructions="You are a helpful assistant"
   )

   # Start interactive chat
   await agent.chat()

REPL Features
-------------

Interactive Commands
~~~~~~~~~~~~~~~~~~~~

Common REPL commands:

- ``/help`` - Show available commands
- ``/history`` - View conversation history
- ``/clear`` - Clear conversation context
- ``/save`` - Save conversation to file
- ``/load`` - Load previous conversation
- ``/exit`` or ``/quit`` - Exit REPL

Multi-line Input
~~~~~~~~~~~~~~~~

Support for multi-line messages:

.. code-block:: text

   >>> ```
   ... This is a
   ... multi-line
   ... message
   ... ```

Special Features
~~~~~~~~~~~~~~~~

- **Syntax highlighting**: Code blocks are highlighted
- **Auto-completion**: Tab completion for commands
- **History navigation**: Up/down arrows for previous messages
- **Context preservation**: Maintains conversation state

Advanced REPL Usage
-------------------

Custom REPL Commands
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.repl.team import Repl

   class CustomRepl(Repl):
       def __init__(self, team):
           super().__init__(team)
           self.register_command("/status", self.show_status)
           self.register_command("/switch", self.switch_agent)
       
       async def show_status(self):
           """Show team status"""
           print(f"Active agents: {len(self.team.agents)}")
           for name, agent in self.team.agents.items():
               print(f"  - {name}: Ready")
       
       async def switch_agent(self, agent_name):
           """Switch to specific agent"""
           if agent_name in self.team.agents:
               self.current_agent = self.team.agents[agent_name]
               print(f"Switched to {agent_name}")

   # Use custom REPL
   repl = CustomRepl(team)
   await repl.run()

REPL with Logging
~~~~~~~~~~~~~~~~~

.. code-block:: python

   import logging

   # Enable debug logging
   logging.basicConfig(level=logging.DEBUG)

   repl = Repl(team)
   
   # Log all interactions
   repl.enable_logging("conversation.log")
   
   await repl.run()

Integration Examples
--------------------

Testing Agents
~~~~~~~~~~~~~~

.. code-block:: python

   # Test agent responses
   test_agent = Agent(
       name="test_agent",
       instructions="You are being tested"
   )

   # Interactive testing
   print("Starting test session...")
   await test_agent.chat()

Development Workflow
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Development REPL with hot reload
   from importlib import reload

   class DevRepl(Repl):
       async def reload_agent(self, agent_name):
           """Reload agent configuration"""
           # Reload agent module
           reload(agent_module)
           
           # Update agent
           self.team.agents[agent_name] = create_agent()
           print(f"Reloaded {agent_name}")

Team Interaction Modes
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Sequential team REPL
   from pantheon.team import SequentialTeam

   seq_team = SequentialTeam([agent1, agent2])
   repl = Repl(seq_team)
   await repl.run()

   # Swarm team with transfers
   from pantheon.team import SwarmTeam

   @agent1.tool
   def transfer_to_agent2():
       return agent2

   swarm_team = SwarmTeam([agent1, agent2])
   repl = Repl(swarm_team)
   await repl.run()

Best Practices
--------------

1. **Clear Prompts**: Use descriptive prompts for better UX
2. **Error Handling**: Gracefully handle errors in REPL
3. **Command Documentation**: Document custom commands
4. **State Management**: Save important conversations
5. **Testing**: Use REPL for agent testing and debugging

Customization Options
---------------------

UI Customization
~~~~~~~~~~~~~~~~

.. code-block:: python

   repl = Repl(
       team,
       # Colors and formatting
       prompt_color="cyan",
       response_color="green",
       error_color="red",
       
       # Display options
       show_agent_name=True,
       show_timestamp=True,
       
       # Behavior
       auto_save=True,
       save_path="./conversations"
   )

Event Handling
~~~~~~~~~~~~~~

.. code-block:: python

   class EventRepl(Repl):
       async def on_message_sent(self, message):
           """Called when user sends a message"""
           print(f"[SENT] {message}")
       
       async def on_response_received(self, response):
           """Called when agent responds"""
           print(f"[RECEIVED] {response.agent_name}: {response.content}")
       
       async def on_error(self, error):
           """Called on errors"""
           print(f"[ERROR] {error}")

Future Enhancements
-------------------

Planned features:

- Rich text formatting
- File uploads in REPL
- Voice input/output
- Multi-user REPL sessions
- Web-based REPL interface