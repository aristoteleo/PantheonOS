Team Module
===========

.. module:: pantheon.team

The team module provides classes for multi-agent collaboration.

Base Team Class
---------------

.. autoclass:: pantheon.team.Team
   :members:
   :undoc-members:
   :show-inheritance:
   :abstract:

Team Types
----------

SequentialTeam
~~~~~~~~~~~~~~

.. autoclass:: pantheon.team.SequentialTeam
   :members:
   :undoc-members:
   :show-inheritance:

   Agents execute one after another in sequence.

   **Example:**

   .. code-block:: python

      from pantheon.team import SequentialTeam
      from pantheon.agent import Agent

      researcher = Agent(name="researcher", instructions="Research topics")
      writer = Agent(name="writer", instructions="Write summaries")
      
      team = SequentialTeam([researcher, writer])
      await team.chat("Research and summarize AI trends")

SwarmTeam
~~~~~~~~~

.. autoclass:: pantheon.team.SwarmTeam
   :members:
   :undoc-members:
   :show-inheritance:

   Agents can dynamically transfer control to each other.

   **Key Features:**
   
   - Dynamic handoffs between agents
   - Agents decide when to transfer
   - Flexible routing based on context

   **Example:**

   .. code-block:: python

      from pantheon.team import SwarmTeam

      agent1 = Agent(name="Agent1", instructions="First agent")
      agent2 = Agent(name="Agent2", instructions="Second agent")

      @agent1.tool
      def transfer_to_agent2():
          """Transfer control to Agent2."""
          return agent2

      team = SwarmTeam([agent1, agent2])
      await team.chat()

SwarmCenterTeam
~~~~~~~~~~~~~~~

.. autoclass:: pantheon.team.SwarmCenterTeam
   :members:
   :undoc-members:
   :show-inheritance:

   A central coordinator manages worker agents.

   **Structure:**
   
   - First agent is the coordinator
   - Remaining agents are workers
   - Coordinator distributes tasks

   **Example:**

   .. code-block:: python

      from pantheon.team import SwarmCenterTeam

      coordinator = Agent(
          name="coordinator",
          instructions="Coordinate tasks between workers"
      )
      worker1 = Agent(name="worker1", instructions="Handle type A tasks")
      worker2 = Agent(name="worker2", instructions="Handle type B tasks")

      team = SwarmCenterTeam([coordinator, worker1, worker2])

MoATeam
~~~~~~~

.. autoclass:: pantheon.team.MoATeam
   :members:
   :undoc-members:
   :show-inheritance:

   Mixture of Agents - multiple agents propose, final agent synthesizes.

   **Structure:**
   
   - All agents except last: Propose solutions
   - Last agent: Synthesize best ideas
   - Parallel proposal generation

   **Example:**

   .. code-block:: python

      from pantheon.team import MoATeam

      expert1 = Agent(name="expert1", instructions="Provide approach 1")
      expert2 = Agent(name="expert2", instructions="Provide approach 2")
      synthesizer = Agent(
          name="synthesizer",
          instructions="Combine best ideas from all experts"
      )

      team = MoATeam([expert1, expert2, synthesizer])

Common Methods
--------------

All team types share these methods:

.. method:: chat(message: str | dict | None = None)

   Start an interactive chat session with the team.

   :param message: Optional initial message
   :type message: str | dict | None

.. method:: run(msg: AgentInput, **kwargs)

   Execute a single task with the team.

   :param msg: Input message or data
   :type msg: AgentInput
   :return: Team execution result
   :rtype: AgentResponse

.. attribute:: events_queue

   Async queue containing team events during execution.

   :type: asyncio.Queue

Team Selection Guide
--------------------

.. list-table::
   :widths: 20 40 40
   :header-rows: 1

   * - Team Type
     - Best For
     - Example Use Cases
   * - Sequential
     - Pipeline workflows
     - Research → Analysis → Report
   * - Swarm
     - Dynamic routing
     - Customer support with escalation
   * - SwarmCenter
     - Task distribution
     - Project management systems
   * - MoA
     - Consensus building
     - Complex decision making

Advanced Examples
-----------------

Customer Support Team
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SwarmTeam
   from pantheon.agent import Agent

   # Create specialized support agents
   general = Agent(
       name="general_support",
       instructions="Handle general inquiries, transfer when needed"
   )
   
   technical = Agent(
       name="technical_support",
       instructions="Handle technical issues"
   )
   
   billing = Agent(
       name="billing_support",
       instructions="Handle billing questions"
   )

   # Add transfer capabilities
   @general.tool
   def transfer_to_technical():
       """Transfer to technical support."""
       return technical

   @general.tool
   def transfer_to_billing():
       """Transfer to billing support."""
       return billing

   # Back transfers
   @technical.tool
   def transfer_to_general():
       """Transfer back to general support."""
       return general

   @billing.tool
   def transfer_to_general():
       """Transfer back to general support."""
       return general

   # Create support team
   support_team = SwarmTeam([general, technical, billing])
   
   # Start support session
   await support_team.chat("I need help with my account")

Research and Writing Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SequentialTeam

   # Create pipeline agents
   researcher = Agent(
       name="researcher",
       instructions="Research topics thoroughly using available sources"
   )
   
   analyst = Agent(
       name="analyst", 
       instructions="Analyze research findings and identify key insights"
   )
   
   writer = Agent(
       name="writer",
       instructions="Write clear, engaging content based on analysis"
   )
   
   editor = Agent(
       name="editor",
       instructions="Edit for clarity, grammar, and style"
   )

   # Create pipeline team
   pipeline = SequentialTeam([researcher, analyst, writer, editor])
   
   # Execute pipeline
   result = await pipeline.run("Create article about quantum computing")

Consensus Building Team
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import MoATeam

   # Domain experts
   security_expert = Agent(
       name="security_expert",
       instructions="Evaluate from security perspective"
   )
   
   performance_expert = Agent(
       name="performance_expert",
       instructions="Evaluate from performance perspective"
   )
   
   ux_expert = Agent(
       name="ux_expert",
       instructions="Evaluate from user experience perspective"
   )
   
   # Synthesizer
   architect = Agent(
       name="architect",
       instructions="Synthesize all perspectives into balanced solution"
   )

   # Create consensus team
   design_team = MoATeam([
       security_expert,
       performance_expert, 
       ux_expert,
       architect
   ])
   
   # Get consensus on design
   result = await design_team.run(
       "Design a new authentication system"
   )