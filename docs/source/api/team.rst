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

PantheonTeam
------------

.. autoclass:: pantheon.team.PantheonTeam
   :members:
   :undoc-members:
   :show-inheritance:

   Runs a conversation's agents in one of two modes:

   - **Team mode** (``mode="team"``, default): every agent gets ``list_agents()`` and ``call_agent()`` to discover and delegate to the others
   - **Agent mode** (``mode="agent"``): only the first agent converses; the others are sub-agent profiles it starts with ``call_agent()``

   **Example:**

   .. code-block:: python

      from pantheon.team import PantheonTeam
      from pantheon.agent import Agent

      researcher = Agent(name="researcher", instructions="Research topics")
      writer = Agent(name="writer", instructions="Write summaries")

      # Team mode: agents delegate to each other
      team = PantheonTeam([researcher, writer])
      await team.chat("Research and summarize AI trends")

      # Agent mode: the researcher converses, the writer is a sub-agent profile
      team = PantheonTeam([researcher, writer], mode="agent")

Common Methods
--------------

All teams share these methods:

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

Example
-------

Research and Writing Team
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import PantheonTeam
   from pantheon.agent import Agent

   researcher = Agent(
       name="researcher",
       instructions="Research topics and delegate analysis and writing"
   )

   analyst = Agent(
       name="analyst",
       instructions="Analyze research findings and identify key insights",
       description="Analyzes findings and extracts key insights"
   )

   writer = Agent(
       name="writer",
       instructions="Write clear, engaging content based on analysis",
       description="Writes articles from research and analysis"
   )

   # The researcher receives the message and delegates with call_agent()
   team = PantheonTeam([researcher, analyst, writer])

   result = await team.run("Create article about quantum computing")
