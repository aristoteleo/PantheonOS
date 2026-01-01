PantheonTeam
============

PantheonTeam is the recommended team structure for multi-agent collaboration. It provides intelligent task delegation through built-in ``list_agents()`` and ``call_agent()`` tools.

Overview
--------

PantheonTeam features:

- **Unified Architecture**: All agents are equal peers with the same capabilities
- **Dynamic Delegation**: Agents can discover and delegate tasks to each other
- **Automatic Tool Injection**: ``list_agents()`` and ``call_agent()`` added automatically
- **Loop Prevention**: Built-in detection prevents circular delegation
- **Context Compression**: Optional automatic context management

Basic Usage
-----------

Creating a PantheonTeam
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import PantheonTeam
   from pantheon import Agent

   # Create specialized agents
   researcher = Agent(
       name="researcher",
       instructions="Research topics thoroughly using available tools.",
       model="gpt-4o-mini"
   )

   analyst = Agent(
       name="analyst",
       instructions="Analyze data and provide insights.",
       model="gpt-4o-mini"
   )

   writer = Agent(
       name="writer",
       instructions="Write clear, engaging content based on research.",
       model="gpt-4o-mini"
   )

   # Create team - first agent is the entry point
   team = PantheonTeam([researcher, analyst, writer])

   # Interactive chat
   await team.chat()

   # Or run a single task
   result = await team.run("Write an article about renewable energy")

Constructor Parameters
----------------------

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Parameter
     - Type
     - Description
   * - ``agents``
     - list[Agent]
     - List of agents in the team. First agent is the default entry point.
   * - ``use_summary``
     - bool
     - If True, generate context summary when delegating tasks. Default: False.
   * - ``max_delegate_depth``
     - int | None
     - Maximum depth for nested ``call_agent`` calls. Default: 5.
   * - ``allow_transfer``
     - bool
     - If True, add ``transfer_to_agent`` tool for control handoff. Default: False.
   * - ``learning_pipeline``
     - LearningPipeline
     - Optional learning pipeline for trajectory learning.

Built-in Tools
--------------

When a team has multiple agents (len > 1), each agent automatically receives:

list_agents()
~~~~~~~~~~~~~

Discover other agents in the team.

.. code-block:: python

   # The agent can call this to see available peers
   # Returns a list of agent names and descriptions

   # Example output:
   # **Available Agents:**
   # - **analyst**: Analyze data and provide insights.
   # - **writer**: Write clear, engaging content.

call_agent()
~~~~~~~~~~~~

Delegate a task to another agent.

.. code-block:: python

   # The agent can delegate work to specialists
   call_agent(
       agent_name="analyst",
       instruction="""
       Goal: Analyze the renewable energy market trends.

       Context: Focus on solar and wind energy sectors.
       Use data from the past 5 years.

       Expected Outcome: Summary report with key statistics.
       """
   )

The ``instruction`` should include:

- **Goal**: What needs to be accomplished
- **Context**: Background information the target agent needs
- **Expected Outcome**: Format or deliverables expected

transfer_to_agent() (Optional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``allow_transfer=True``, agents can hand off control:

.. code-block:: python

   team = PantheonTeam(
       [researcher, analyst, writer],
       allow_transfer=True
   )

   # Now agents can transfer control to each other
   # transfer_to_agent("writer")

How It Works
------------

1. **Entry Point**: The first agent in the list receives the initial message
2. **Discovery**: Agents use ``list_agents()`` to find specialists
3. **Delegation**: Agents use ``call_agent()`` to delegate subtasks
4. **Loop Prevention**: The system prevents agents from delegating back to themselves in the same chain
5. **Depth Limit**: Nested delegations are limited by ``max_delegate_depth``

.. code-block:: text

   User Message
        |
        v
   [Researcher] (entry point)
        |
        +---> list_agents() --> discovers analyst, writer
        |
        +---> call_agent("analyst", "Analyze X...")
        |          |
        |          v
        |     [Analyst] performs analysis
        |          |
        |          v
        |     Returns result to Researcher
        |
        +---> call_agent("writer", "Write about X...")
        |          |
        |          v
        |     [Writer] writes content
        |          |
        |          v
        |     Returns result to Researcher
        |
        v
   Final Response to User

Examples
--------

Research Team
~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.team import PantheonTeam
   from pantheon import Agent
   from pantheon.toolsets import WebToolSet, FileManagerToolSet

   researcher = Agent(
       name="researcher",
       instructions="""You are a research specialist.
       Use web search to find information.
       Delegate analysis to the analyst when needed.""",
       tools=[WebToolSet()]
   )

   analyst = Agent(
       name="analyst",
       instructions="""You analyze information and extract insights.
       Focus on accuracy and supporting evidence."""
   )

   writer = Agent(
       name="writer",
       instructions="""You write clear, well-structured reports.
       Use markdown formatting.""",
       tools=[FileManagerToolSet()]
   )

   team = PantheonTeam([researcher, analyst, writer])
   await team.chat()

Development Team
~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets import ShellToolSet, PythonInterpreterToolSet

   architect = Agent(
       name="architect",
       instructions="Design system architecture and delegate implementation."
   )

   developer = Agent(
       name="developer",
       instructions="Implement code based on specifications.",
       tools=[FileManagerToolSet(), PythonInterpreterToolSet()]
   )

   reviewer = Agent(
       name="reviewer",
       instructions="Review code for quality and best practices."
   )

   dev_team = PantheonTeam([architect, developer, reviewer])

With Context Summary
~~~~~~~~~~~~~~~~~~~~

Enable context summarization for delegated tasks:

.. code-block:: python

   team = PantheonTeam(
       [agent1, agent2, agent3],
       use_summary=True  # Summarize conversation history when delegating
   )

Best Practices
--------------

1. **Clear Agent Roles**: Give each agent a distinct, focused responsibility
2. **Descriptive Names**: Use names that reflect the agent's specialty
3. **Detailed Instructions**: Include guidance on when to delegate
4. **Appropriate Team Size**: Keep teams between 2-5 agents for efficiency
5. **First Agent as Coordinator**: The entry point agent should understand the full workflow

Agent Descriptions
------------------

Add descriptions to help agents discover each other:

.. code-block:: python

   analyst = Agent(
       name="analyst",
       instructions="...",
       description="Data analysis specialist for statistical work"
   )

The description appears in ``list_agents()`` output to help agents choose the right delegate.
