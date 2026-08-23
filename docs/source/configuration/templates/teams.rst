Team Templates
==============

Team templates define multi-agent configurations for collaborative tasks.

Location
--------

Team templates are stored in ``.pantheon/teams/``:

.. code-block:: text

   .pantheon/
   └── teams/
       ├── my_team.md
       ├── research_team.md
       └── data_team.md

Template Format
---------------

Basic Structure
~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: My Team
   team_type: pantheon
   agents:
     - name: agent1
       model: openai/gpt-4o
       instructions: First agent instructions.
     - name: agent2
       model: openai/gpt-4o-mini
       instructions: Second agent instructions.
   ---

   # Team Description

   This team works on collaborative tasks.

Frontmatter Fields
~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Required
     - Description
   * - ``name``
     - Yes
     - Display name for the team
   * - ``team_type``
     - No
     - Team type: ``pantheon``, ``swarm``, ``sequential``, ``moa``
   * - ``agents``
     - Yes
     - List of agent configurations
   * - ``model``
     - No
     - Default model for all agents
   * - ``orchestrator``
     - No
     - Orchestrator configuration (for pantheon teams)

Agent Configuration
~~~~~~~~~~~~~~~~~~~

Each agent in the ``agents`` list:

.. code-block:: yaml

   agents:
     - name: researcher        # Required: agent name
       model: openai/gpt-4o    # Optional: override default model
       icon: 🔍                # Optional: display icon
       instructions: |         # Required: agent instructions
         You are a researcher.
         Search for information.
       toolsets:               # Optional: agent-specific tools
         - web_browse
       mcp_servers:            # Optional: MCP servers
         - github

Team Types
----------

Pantheon Team (Default)
~~~~~~~~~~~~~~~~~~~~~~~

Orchestrator-managed team with dynamic task delegation:

.. code-block:: markdown

   ---
   name: Development Team
   team_type: pantheon
   orchestrator:
     model: openai/gpt-4o
     instructions: Coordinate the development process.
   agents:
     - name: architect
       instructions: Design system architecture.
     - name: developer
       instructions: Implement features.
       toolsets:
         - file_manager
         - shell
     - name: reviewer
       instructions: Review code quality.
   ---

   A team for software development projects.

Swarm Team
~~~~~~~~~~

Agents can hand off to each other dynamically:

.. code-block:: markdown

   ---
   name: Support Team
   team_type: swarm
   agents:
     - name: triage
       instructions: |
         Assess customer issues.
         Hand off to specialist or support.
       handoffs:
         - specialist
         - support
     - name: specialist
       instructions: Handle technical issues.
     - name: support
       instructions: Handle general inquiries.
   ---

   Customer support with dynamic routing.

Sequential Team
~~~~~~~~~~~~~~~

Agents process tasks in order:

.. code-block:: markdown

   ---
   name: Content Pipeline
   team_type: sequential
   agents:
     - name: researcher
       instructions: Research the topic.
     - name: writer
       instructions: Write the content.
     - name: editor
       instructions: Edit and polish.
   ---

   Sequential content creation pipeline.

MoA Team (Mixture of Agents)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Multiple agents contribute, then aggregate:

.. code-block:: markdown

   ---
   name: Analysis Team
   team_type: moa
   agents:
     - name: analyst1
       model: openai/gpt-4o
       instructions: Analyze from business perspective.
     - name: analyst2
       model: anthropic/claude-3-opus
       instructions: Analyze from technical perspective.
     - name: analyst3
       model: openai/gpt-4o
       instructions: Analyze from user perspective.
   aggregator:
     model: openai/gpt-4o
     instructions: Synthesize all perspectives.
   ---

   Multi-perspective analysis team.

Examples
--------

Developer Team
~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Developer Team
   team_type: pantheon
   model: openai/gpt-4o
   agents:
     - name: planner
       icon: 📋
       instructions: |
         You are a project planner.
         Break down tasks into steps.
         Create implementation plans.

     - name: developer
       icon: 👨‍💻
       instructions: |
         You are an expert developer.
         Write clean, tested code.
         Follow best practices.
       toolsets:
         - file_manager
         - shell
         - python_interpreter

     - name: reviewer
       icon: 🔍
       instructions: |
         You are a code reviewer.
         Check for bugs and issues.
         Suggest improvements.
       toolsets:
         - file_manager
   ---

   # Developer Team

   A team for software development with planning,
   implementation, and review phases.

Research Team
~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Research Team
   team_type: sequential
   agents:
     - name: searcher
       icon: 🔎
       instructions: Search for relevant information.
       toolsets:
         - web_browse

     - name: analyst
       icon: 📊
       instructions: Analyze and summarize findings.

     - name: writer
       icon: ✍️
       instructions: Write clear research reports.
       toolsets:
         - file_manager
   ---

   # Research Team

   Sequential research pipeline: search → analyze → report.

Data Analysis Team
~~~~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Data Team
   team_type: pantheon
   agents:
     - name: engineer
       icon: 🔧
       instructions: |
         Data engineering tasks.
         Clean and transform data.
       toolsets:
         - python_interpreter
         - file_manager

     - name: analyst
       icon: 📈
       instructions: |
         Statistical analysis.
         Generate insights.
       toolsets:
         - python_interpreter
         - notebook

     - name: visualizer
       icon: 🎨
       instructions: |
         Create visualizations.
         Design charts and dashboards.
       toolsets:
         - python_interpreter
         - notebook
   ---

   # Data Analysis Team

   Comprehensive data analysis with engineering,
   analysis, and visualization capabilities.

Usage
-----

**REPL:**

.. code-block:: bash

   pantheon cli --template .pantheon/teams/default.md

**ChatRoom:**

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

Then select the team from the **Team** dropdown. ``pantheon ui`` has no
``--template`` flag.

**Python API:**

.. code-block:: python

   from pantheon.factory import create_team_from_template
   from pantheon.endpoint import Endpoint

   endpoint = Endpoint()
   team = await create_team_from_template(endpoint, "default")
   response = await team.run("Build a REST API")

Best Practices
--------------

1. **Clear Roles**: Each agent should have a distinct, well-defined role
2. **Minimal Overlap**: Avoid giving agents overlapping responsibilities
3. **Appropriate Tools**: Only give agents the tools they need
4. **Right Team Type**: Choose the team type that matches your workflow
5. **Good Instructions**: Provide clear, specific instructions for each agent
