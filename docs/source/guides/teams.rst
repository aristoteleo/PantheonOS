Teams Guide
===========

Teams enable multiple agents to collaborate on tasks. Pantheon supports several team patterns.

Team Types
----------

Sequential Team
~~~~~~~~~~~~~~~

Agents execute one after another, passing results forward:

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.team import SequentialTeam

   # Create agents
   researcher = Agent(
       name="researcher",
       instructions="Research the topic thoroughly."
   )
   
   writer = Agent(
       name="writer", 
       instructions="Write based on the research."
   )
   
   # Create sequential team
   team = SequentialTeam([researcher, writer])
   
   # Run the team
   await team.chat("Write about AI trends")

Swarm Team
~~~~~~~~~~

Agents can dynamically transfer control to each other:

.. code-block:: python

   from pantheon.team import SwarmTeam
   from pantheon.repl.team import Repl

   # Create agents with transfer capabilities
   agent1 = Agent(name="Agent1", instructions="First agent")
   agent2 = Agent(name="Agent2", instructions="Second agent")

   @agent1.tool
   def transfer_to_agent2():
       """Transfer to Agent2 when needed."""
       return agent2

   @agent2.tool  
   def transfer_to_agent1():
       """Transfer back to Agent1."""
       return agent1

   # Create swarm
   team = SwarmTeam([agent1, agent2])
   
   # Interactive chat
   repl = Repl(team)
   await repl.run()

SwarmCenter Team
~~~~~~~~~~~~~~~~

A central coordinator manages multiple worker agents:

.. code-block:: python

   from pantheon.team import SwarmCenterTeam

   # Coordinator (first agent)
   coordinator = Agent(
       name="coordinator",
       instructions="Coordinate tasks between workers."
   )

   # Worker agents
   worker1 = Agent(name="worker1", instructions="Handle task type A")
   worker2 = Agent(name="worker2", instructions="Handle task type B")

   # Create team with coordinator first
   team = SwarmCenterTeam([coordinator, worker1, worker2])

MoA (Mixture of Agents) Team
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Multiple agents provide solutions, then a final agent synthesizes:

.. code-block:: python

   from pantheon.team import MoATeam

   # Expert agents
   expert1 = Agent(name="expert1", instructions="Provide solution approach 1")
   expert2 = Agent(name="expert2", instructions="Provide solution approach 2")
   
   # Synthesizer (last agent)
   synthesizer = Agent(
       name="synthesizer",
       instructions="Combine the best ideas from all experts."
   )

   # Create MoA team
   team = MoATeam([expert1, expert2, synthesizer])

Using Teams
-----------

Interactive Chat
~~~~~~~~~~~~~~~~

All teams support interactive chat:

.. code-block:: python

   # Any team type
   await team.chat()
   
   # With initial message
   await team.chat("Hello team!")

Programmatic Execution
~~~~~~~~~~~~~~~~~~~~~~

For programmatic use:

.. code-block:: python

   # Run with a specific message
   result = await team.run("Solve this problem")
   
   # Access individual agent responses
   print(result)

Team Events
~~~~~~~~~~~

Teams emit events during execution:

.. code-block:: python

   # Access team event queue
   while not team.events_queue.empty():
       event = await team.events_queue.get()
       print(f"Agent: {event['agent_name']}, Event: {event['event']}")

Practical Examples
------------------

Book Recommendation Team
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent
   from pantheon.team import SequentialTeam

   # Specialized agents
   scifi_expert = Agent(
       name="scifi_expert",
       instructions="You are a science fiction expert. Recommend scifi books."
   )

   romance_expert = Agent(
       name="romance_expert", 
       instructions="You are a romance expert. Recommend romance books."
   )

   # Sequential team for diverse recommendations
   team = SequentialTeam([scifi_expert, romance_expert])
   
   asyncio.run(team.chat("I want book recommendations"))

Customer Support Team
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SwarmTeam

   # Support agents with specializations
   general_support = Agent(
       name="general_support",
       instructions="Handle general inquiries. Transfer to specialists when needed."
   )

   technical_support = Agent(
       name="technical_support",
       instructions="Handle technical issues."
   )

   billing_support = Agent(
       name="billing_support",
       instructions="Handle billing questions."
   )

   # Add transfer functions
   @general_support.tool
   def transfer_to_technical():
       """Transfer to technical support."""
       return technical_support

   @general_support.tool
   def transfer_to_billing():
       """Transfer to billing support."""
       return billing_support

   # Create support team
   team = SwarmTeam([general_support, technical_support, billing_support])

Best Practices
--------------

1. **Choose the Right Pattern**:
   - Sequential: For pipeline workflows
   - Swarm: For dynamic handoffs
   - SwarmCenter: For task distribution
   - MoA: For consensus/synthesis

2. **Clear Agent Roles**: Each agent should have a specific purpose

3. **Transfer Logic**: In Swarm teams, make transfer conditions explicit

4. **Error Handling**: Consider what happens if an agent fails

5. **Testing**: Test teams with various inputs before production use

Next Steps
----------

- Explore :doc:`chatroom` for web interfaces
- Check :doc:`../examples/index` for more team examples
- Read about :doc:`agents` for agent configuration