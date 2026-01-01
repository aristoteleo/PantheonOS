SwarmCenter Team
================

SwarmCenterTeam implements a hub-and-spoke pattern where a central triage agent routes tasks to specialized worker agents. Transfer tools are automatically generated.

Overview
--------

Key characteristics:

- **Central Triage Agent**: One agent manages all task routing
- **Automatic Transfer Tools**: ``transfer_to_{agent_name}`` generated for each worker
- **Automatic Return Tools**: Each worker gets ``transfer_back_to_triage``
- **Dynamic Agent Management**: Add or remove workers at runtime

Basic Usage
-----------

Creating a SwarmCenter Team
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SwarmCenterTeam
   from pantheon import Agent

   # Create triage agent (the central router)
   triage = Agent(
       name="triage",
       instructions="""You are a task router. Analyze requests and delegate to:
       - researcher: For information gathering
       - developer: For code implementation
       - writer: For documentation""",
       model="gpt-4o"
   )

   # Create specialized workers
   researcher = Agent(
       name="researcher",
       instructions="Research information and provide findings.",
       model="gpt-4o-mini"
   )

   developer = Agent(
       name="developer",
       instructions="Implement code solutions.",
       model="gpt-4o-mini"
   )

   writer = Agent(
       name="writer",
       instructions="Create documentation.",
       model="gpt-4o-mini"
   )

   # Create SwarmCenter team
   team = SwarmCenterTeam(
       triage=triage,
       agents=[researcher, developer, writer]
   )

   # Run the team
   result = await team.run("Create a Python function to parse JSON files")

Constructor Parameters
----------------------

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Parameter
     - Type
     - Description
   * - ``triage``
     - Agent
     - The central triage agent that routes tasks to workers.
   * - ``agents``
     - list[Agent | RemoteAgent]
     - List of worker agents to receive delegated tasks.

Auto-Generated Tools
--------------------

SwarmCenterTeam automatically generates transfer tools:

For the Triage Agent
~~~~~~~~~~~~~~~~~~~~

For each worker agent, a transfer tool is created:

.. code-block:: python

   # Auto-generated for triage agent:
   def transfer_to_researcher():
       """Transfer to researcher."""
       return researcher

   def transfer_to_developer():
       """Transfer to developer."""
       return developer

   def transfer_to_writer():
       """Transfer to writer."""
       return writer

The function name is derived from the agent's name (spaces replaced with underscores, lowercased).

For Each Worker Agent
~~~~~~~~~~~~~~~~~~~~~

Each worker automatically gets a tool to transfer back:

.. code-block:: python

   # Auto-generated for each worker:
   def transfer_back_to_triage():
       """Transfer back to the triage agent."""
       return triage

How It Works
------------

.. code-block:: text

   User Message
        |
        v
   [Triage Agent]
        |
        +---> Analyzes request
        |
        +---> Calls transfer_to_{worker}()
        |          |
        |          v
        |     [Worker Agent]
        |          |
        |          +---> Processes task
        |          |
        |          +---> Calls transfer_back_to_triage()
        |                     |
        |                     v
        +<----------------[Triage Agent]
        |
        +---> Returns final response

Dynamic Agent Management
------------------------

Add Agent at Runtime
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Create initial team
   team = SwarmCenterTeam(triage=triage, agents=[researcher])

   # Add new agent later
   new_agent = Agent(name="analyst", instructions="Analyze data.")
   await team.add_agent(new_agent)

   # Triage now has transfer_to_analyst() tool
   # Analyst has transfer_back_to_triage() tool

Remove Agent at Runtime
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Remove agent from team
   await team.remove_agent(analyst)

   # Triage no longer has transfer_to_analyst() tool

Run Method
----------

.. code-block:: python

   result = await team.run(
       msg="Your task message",
       **kwargs  # Additional kwargs passed to agents
   )

The ``run()`` method:

1. Calls ``async_setup()`` to initialize transfer tools
2. Delegates to ``SwarmTeam.run()`` for execution

Examples
--------

Development Team
~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import SwarmCenterTeam
   from pantheon import Agent
   from pantheon.toolsets import FileManagerToolSet, PythonInterpreterToolSet

   # Tech lead as triage
   tech_lead = Agent(
       name="tech_lead",
       instructions="""Route development tasks:
       - architect: System design and planning
       - developer: Code implementation
       - tester: Testing and QA""",
       model="gpt-4o"
   )

   architect = Agent(
       name="architect",
       instructions="Design system architecture."
   )

   developer = Agent(
       name="developer",
       instructions="Implement code based on specifications.",
       tools=[FileManagerToolSet(), PythonInterpreterToolSet()]
   )

   tester = Agent(
       name="tester",
       instructions="Write and run tests."
   )

   dev_team = SwarmCenterTeam(
       triage=tech_lead,
       agents=[architect, developer, tester]
   )

   result = await dev_team.run("Build a REST API for user management")

Research Team
~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets import WebToolSet

   coordinator = Agent(
       name="coordinator",
       instructions="""Coordinate research:
       - literature_reviewer: Academic paper analysis
       - data_analyst: Data processing
       - writer: Report writing""",
       model="gpt-4o"
   )

   literature_reviewer = Agent(
       name="literature_reviewer",
       instructions="Review and summarize academic papers.",
       tools=[WebToolSet()]
   )

   data_analyst = Agent(
       name="data_analyst",
       instructions="Analyze data and create visualizations."
   )

   writer = Agent(
       name="writer",
       instructions="Write comprehensive research reports."
   )

   research_team = SwarmCenterTeam(
       triage=coordinator,
       agents=[literature_reviewer, data_analyst, writer]
   )

With Remote Agents
~~~~~~~~~~~~~~~~~~

SwarmCenterTeam supports RemoteAgent workers:

.. code-block:: python

   from pantheon import RemoteAgent

   # Remote agent fetches its info automatically
   remote_specialist = RemoteAgent(service_id="specialist-service")

   team = SwarmCenterTeam(
       triage=triage,
       agents=[local_agent, remote_specialist]
   )

   # await team.add_agent(remote_specialist) will call fetch_info()

Best Practices
--------------

1. **Clear Triage Instructions**: Define routing rules in triage agent's instructions
2. **Distinct Worker Roles**: Each worker should have specialized expertise
3. **Descriptive Agent Names**: Names become part of transfer function names
4. **Return to Triage**: Workers should transfer back when done
5. **Appropriate Team Size**: Keep to 3-6 workers for effective routing

SwarmTeam vs SwarmCenterTeam
----------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Feature
     - SwarmTeam
     - SwarmCenterTeam
   * - Transfer Tools
     - Manual definition
     - Auto-generated
   * - Structure
     - Any-to-any transfers
     - Hub-and-spoke pattern
   * - Central Agent
     - Optional
     - Required (triage)
   * - Return Pattern
     - Custom
     - Auto ``transfer_back_to_triage``
   * - Use Case
     - Flexible workflows
     - Centralized task routing
