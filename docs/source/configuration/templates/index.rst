Templates
=========

Templates define reusable agent and team configurations using Markdown with YAML frontmatter.

Overview
--------

Templates are stored in ``.pantheon/``:

- **Agent templates**: ``.pantheon/agents/*.md``
- **Team templates**: ``.pantheon/teams/*.md``
- **Prompt snippets**: ``.pantheon/prompts/*``

Template Format
---------------

All templates use Markdown with YAML frontmatter:

.. code-block:: markdown

   ---
   name: My Agent
   model: openai/gpt-4o
   icon: 🤖
   toolsets:
     - file_manager
   ---

   # Instructions

   You are a helpful assistant.

   ## Your Responsibilities
   - Help users with their tasks
   - Be concise and accurate

Quick Start
-----------

**Create an agent:**

.. code-block:: bash

   mkdir -p .pantheon/agents

Create ``.pantheon/agents/helper.md``:

.. code-block:: markdown

   ---
   name: Helper
   model: openai/gpt-4o-mini
   ---

   You are a helpful assistant.

**Use the agent:**

.. code-block:: bash

   python -m pantheon.repl --template helper

**Create a team:**

Create ``.pantheon/teams/my_team.md``:

.. code-block:: markdown

   ---
   name: My Team
   agents:
     - name: researcher
       model: openai/gpt-4o-mini
       instructions: Research topics.
     - name: writer
       model: openai/gpt-4o-mini
       instructions: Write content.
   ---

   # My Team

   A team for research and writing.

Prompt Snippets
---------------

Reusable text in ``.pantheon/prompts/``:

Create ``.pantheon/prompts/work_strategy``:

.. code-block:: text

   ## Work Strategy
   1. Understand the task
   2. Break it into steps
   3. Execute carefully
   4. Verify results

Reference in templates:

.. code-block:: markdown

   ---
   name: Worker
   ---

   You are a worker.

   {{work_strategy}}

The ``{{work_strategy}}`` is replaced with the prompt content.

System Templates
----------------

Pantheon includes built-in templates:

- ``default`` - General assistant
- ``developer_team`` - Development team
- ``data_research_team`` - Data analysis team

These are used when no custom template is specified.

Next Steps
----------

- :doc:`agents` - Agent template reference
- :doc:`teams` - Team template reference

.. toctree::
   :hidden:
   :maxdepth: 1

   agents
   teams
