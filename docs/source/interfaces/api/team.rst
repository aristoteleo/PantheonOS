Team API
========

Teams enable multiple agents to collaborate on complex tasks.

Team Modes
----------

``pantheon.team`` provides ``PantheonTeam`` (and the base ``Team`` class for custom teams). ``PantheonTeam`` runs in one of two modes:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Mode
     - Description
   * - ``mode="team"`` (default)
     - Smart delegation: agents call each other with ``call_agent()`` and discover each other with ``list_agents()``
   * - ``mode="agent"``
     - Only the first agent converses; the others are sub-agent profiles it starts with ``call_agent()``

Team Mode
---------

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

The first agent in the list receives the user's message. Each agent automatically gets:

- ``call_agent(agent_name, instruction)``: Delegate to another agent
- ``list_agents()``: See available agents

Agent Mode
----------

One agent converses; the other agents are sub-agent profiles it starts on self-contained tasks. Each sub-agent runs in its own fresh context and only its final answer comes back.

.. code-block:: python

   leader = Agent(name="leader", instructions="Work on the user's task directly.")
   researcher = Agent(
       name="researcher",
       instructions="Search the web and report findings.",
       description="Web research on a self-contained question"
   )

   team = PantheonTeam([leader, researcher], mode="agent")

Only the first agent gets ``call_agent(agent_name, instruction)``; its description lists the sub-agent profiles.

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
   team = await create_team_from_template(endpoint, "data_research_team")

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

PantheonTeam handles most use cases well. For other collaboration patterns, subclass ``pantheon.team.Team``.
