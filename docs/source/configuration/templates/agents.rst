Agent Templates
===============

Agent templates define reusable agent configurations using Markdown with YAML frontmatter.

Location
--------

Agent templates are stored in ``.pantheon/agents/``:

.. code-block:: text

   .pantheon/
   └── agents/
       ├── assistant.md
       ├── developer.md
       └── researcher.md

Template Format
---------------

Basic Structure
~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: My Agent
   model: openai/gpt-4o
   icon: 🤖
   ---

   # Instructions

   You are a helpful assistant.

   ## Your Responsibilities
   - Help users with their tasks
   - Be concise and accurate

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
     - Display name for the agent
   * - ``model``
     - No
     - Model to use (e.g., ``openai/gpt-4o``, ``anthropic/claude-3-opus``)
   * - ``icon``
     - No
     - Emoji icon for display
   * - ``toolsets``
     - No
     - List of toolsets to enable
   * - ``mcp_servers``
     - No
     - List of MCP servers to connect
   * - ``temperature``
     - No
     - Model temperature (0.0-2.0)
   * - ``max_tokens``
     - No
     - Maximum response tokens

Instructions Section
~~~~~~~~~~~~~~~~~~~~

The markdown content after the frontmatter becomes the agent's system instructions.

Use clear, structured instructions:

.. code-block:: markdown

   ---
   name: Code Reviewer
   model: openai/gpt-4o
   ---

   You are an expert code reviewer.

   ## Your Role
   Review code for:
   - Correctness and bugs
   - Performance issues
   - Security vulnerabilities
   - Code style and best practices

   ## Review Format
   For each issue found:
   1. Describe the problem
   2. Explain why it's an issue
   3. Suggest a fix

   ## Guidelines
   - Be constructive, not critical
   - Prioritize important issues
   - Acknowledge good patterns

Examples
--------

Developer Agent
~~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Developer
   model: openai/gpt-4o
   icon: 👨‍💻
   toolsets:
     - file_manager
     - shell
     - python_interpreter
   ---

   You are an expert software developer.

   ## Capabilities
   - Write clean, well-documented code
   - Debug and fix issues
   - Refactor for better design

   ## Guidelines
   - Follow project conventions
   - Write tests for new features
   - Keep changes minimal and focused

Research Agent
~~~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Researcher
   model: openai/gpt-4o
   icon: 🔍
   toolsets:
     - web_browse
     - file_manager
   ---

   You are a research assistant.

   ## Your Role
   - Search for information
   - Summarize findings
   - Cite sources

   ## Output Format
   Present findings with:
   - Key points summary
   - Detailed analysis
   - Source references

Data Analyst
~~~~~~~~~~~~

.. code-block:: markdown

   ---
   name: Data Analyst
   model: openai/gpt-4o
   icon: 📊
   toolsets:
     - python_interpreter
     - notebook
     - file_manager
   ---

   You are a data analysis expert.

   ## Capabilities
   - Data cleaning and transformation
   - Statistical analysis
   - Visualization creation

   ## Tools
   Use pandas, numpy, matplotlib, seaborn for analysis.

Using Toolsets
--------------

Enable built-in toolsets:

.. code-block:: yaml

   toolsets:
     - file_manager      # File read/write/search
     - shell             # Shell command execution
     - python_interpreter # Python code execution
     - notebook          # Jupyter notebook operations
     - web_browse        # Web search and fetch

Using MCP Servers
-----------------

Connect to MCP servers defined in ``mcp.json``:

.. code-block:: yaml

   mcp_servers:
     - filesystem        # Server name from mcp.json
     - github

Using Prompt Snippets
---------------------

Reference reusable prompts with ``{{snippet_name}}``:

.. code-block:: markdown

   ---
   name: Worker
   model: openai/gpt-4o
   ---

   You are a task worker.

   {{work_strategy}}

   {{output_format}}

The snippets are loaded from ``.pantheon/prompts/work_strategy`` and ``.pantheon/prompts/output_format``.

Usage
-----

**REPL:**

.. code-block:: bash

   pantheon cli --template .pantheon/teams/default.md

**ChatRoom:**

Start the UI, then select a team that references this agent from the **Team**
dropdown. ``pantheon ui`` has no ``--template`` flag.

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

**Python API:**

.. code-block:: python

   from pantheon.factory import create_team_from_template
   from pantheon.endpoint import Endpoint

   endpoint = Endpoint()
   team = await create_team_from_template(endpoint, "default")
   response = await team.run("Help me write a function")

Best Practices
--------------

1. **Clear Role Definition**: Start with a clear statement of who the agent is
2. **Specific Guidelines**: Provide concrete guidelines for behavior
3. **Output Format**: Specify expected output format when relevant
4. **Minimal Toolsets**: Only include toolsets the agent actually needs
5. **Structured Markdown**: Use headers and lists for readability
