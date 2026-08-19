Team API
========

Teams enable multiple agents to collaborate on complex tasks.

Available Teams
---------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Team Type
     - Description
   * - ``PantheonTeam``
     - Smart delegation with ``call_agent()`` (recommended)
   * - ``SwarmTeam``
     - Dynamic agent handoff
   * - ``SequentialTeam``
     - Linear pipeline processing
   * - ``MoATeam``
     - Mixture of Agents (parallel + synthesis)
   * - ``AgentAsToolTeam``
     - Sub-agents as tools

PantheonTeam (Recommended)
--------------------------

Intelligent delegation where agents can call each other.

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.team import PantheonTeam

   researcher = Agent(
       name="researcher",
       instructions="You research topics thoroughly."
   )

   writer = Agent(
       name="writer",
       instructions="You write clear, engaging content."
   )

   team = PantheonTeam([researcher, writer])

   # Run single query
   response = await team.run("Write about AI safety")

   # Interactive chat
   await team.chat()

The first agent in the list becomes the lead. It automatically gets:

- ``call_agent(name, instruction)``: Delegate to another agent
- ``list_agents()``: See available agents

SwarmTeam
---------

Agents transfer control dynamically using transfer functions.

.. code-block:: python

   from pantheon.team import SwarmTeam

   @agent1.tool
   def transfer_to_agent2():
       """Transfer to agent2 for specialized work."""
       return agent2

   @agent2.tool
   def transfer_to_agent1():
       """Transfer back to agent1."""
       return agent1

   team = SwarmTeam([agent1, agent2])

SequentialTeam
--------------

Agents process in order, each building on the previous output.

.. code-block:: python

   from pantheon.team import SequentialTeam

   # researcher -> analyst -> writer
   team = SequentialTeam([researcher, analyst, writer])

MoATeam
-------

Multiple agents work in parallel, then an aggregator synthesizes.

.. code-block:: python

   from pantheon.team import MoATeam

   team = MoATeam(
       agents=[expert1, expert2, expert3],
       aggregator=synthesizer
   )

AgentAsToolTeam
---------------

Leader agent calls sub-agents as tools.

.. code-block:: python

   from pantheon.team import AgentAsToolTeam

   team = AgentAsToolTeam(
       leader=main_agent,
       agents=[specialist1, specialist2]
   )

Common Methods
--------------

All teams support:

**run(message)**

.. code-block:: python

   response = await team.run("Your task here")

**chat()**

.. code-block:: python

   await team.chat()  # Interactive REPL

Creating Teams from Templates
-----------------------------

.. code-block:: python

   from pantheon.factory import create_team_from_template
   from pantheon.endpoint import Endpoint

   endpoint = Endpoint()
   team = await create_team_from_template(endpoint, "single_cell_team")

Best Practices
--------------

**Clear Roles**

Give each agent a distinct responsibility:

.. code-block:: python

   researcher = Agent(
       name="researcher",
       instructions="Focus on finding accurate information."
   )

   critic = Agent(
       name="critic",
       instructions="Review and find issues with the content."
   )

**Appropriate Size**

Keep teams between 2-5 agents. Too many agents can slow down responses.

**Start with PantheonTeam**

Unless you have specific needs, PantheonTeam handles most use cases well.
