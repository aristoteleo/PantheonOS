Team Templates
==============

Team templates define multi-agent configurations for collaborative tasks.

Location
--------

Team templates are stored in ``.pantheon/teams/``:

.. code-block:: text

   .pantheon/
   └── teams/
       ├── developer_team.md
       ├── research_team.md
       └── data_team.md

Template Format
---------------

Basic Structure
~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: My Team
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
   * - ``mode``
     - No
     - ``team`` (default): agents delegate to each other with ``call_agent()``. ``agent``: only the first agent converses; the others are sub-agent profiles it starts with ``call_agent()``
   * - ``agents``
     - Yes
     - List of agent configurations
   * - ``model``
     - No
     - Default model for all agents

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

Team Modes
----------

Team Mode (Default)
~~~~~~~~~~~~~~~~~~~

Every agent can discover the others with ``list_agents()`` and delegate tasks with ``call_agent()``. The first agent receives the user's message:

.. code-block:: markdown

   ---
   name: Development Team
   agents:
     - name: architect
       instructions: Design system architecture and coordinate the work.
     - name: developer
       instructions: Implement features.
       toolsets:
         - file_manager
         - shell
     - name: reviewer
       instructions: Review code quality.
   ---

   A team for software development projects.

Agent Mode
~~~~~~~~~~

Only the first agent converses with the user. The other agents are sub-agent profiles it starts with ``call_agent()`` on self-contained tasks; each runs in its own fresh context and returns only its final answer:

.. code-block:: markdown

   ---
   name: General Team
   mode: agent
   agents:
     - name: leader
       instructions: Work on the user's task directly; start sub-agents for self-contained tasks.
       toolsets:
         - file_manager
         - shell
     - name: researcher
       instructions: Search the web and report findings.
       toolsets:
         - web
   ---

   One agent that works directly and starts sub-agents for self-contained tasks.

Examples
--------

Developer Team
~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Developer Team
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

   Research team: search, analyze and report.

Data Analysis Team
~~~~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Data Team
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

   pantheon cli --template developer_team

**ChatRoom:**

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui --template developer_team

**Python API:**

.. code-block:: python

   from pantheon.factory import load_team

   team = load_team("developer_team")
   response = await team.run("Build a REST API")

Best Practices
--------------

1. **Clear Roles**: Each agent should have a distinct, well-defined role
2. **Minimal Overlap**: Avoid giving agents overlapping responsibilities
3. **Appropriate Tools**: Only give agents the tools they need
4. **Right Mode**: Use team mode for peers that delegate to each other, agent mode for one agent with sub-agents
5. **Good Instructions**: Provide clear, specific instructions for each agent
