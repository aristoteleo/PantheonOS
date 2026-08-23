CLI Quick Start
===============

Get up and running with the Pantheon CLI in under two minutes.

Prerequisites
-------------

- Python 3.10 or later
- Install the package:

  .. code-block:: bash

     pip install pantheon-agents

- At least one provider API key (e.g., ``OPENROUTER_API_KEY``, ``OPENAI_API_KEY``, or
  ``ANTHROPIC_API_KEY``) set as an environment variable or configured via
  ``pantheon cli`` on first run.

Starting the REPL
-----------------

.. code-block:: bash

   pantheon cli

You are dropped into an interactive prompt. Type a message and press **Enter** to send it
to the agent. Responses stream in real time.

.. code-block:: text

   ╭─ Pantheon CLI ────────────────────────────────╮
   │  Model: anthropic/claude-opus-4-5              │
   │  Team:  default                                │
   │  Chat:  new-session-8f3a                       │
   ╰────────────────────────────────────────────────╯

   > Hello! What can you help me with?

Starting with a Specific Team Template
---------------------------------------

.. code-block:: bash

   pantheon cli --template .pantheon/teams/single_cell_team.md

``--template`` takes a **path to a team Markdown file**, not a bare ID. Factory
templates are copied into ``.pantheon/teams/`` on first run (``default.md``,
``single_cell_team.md``, ``paper_write_team.md``, ``omicverse_team.md``,
``rare_disease_team.md``, ``evolution_team.md``). The CLI loads that team before
the first prompt.

Resuming a Previous Session
----------------------------

.. code-block:: bash

   # Resume the most recent session
   pantheon cli --resume last

   # Resume by partial session name
   pantheon cli --resume rna-seq-analysis

   # Resume by exact session ID
   pantheon cli --resume abc1234def

The full conversation history is loaded and you can continue from where you left off.

One-Shot Query (No Interactive Session)
----------------------------------------

Use ``-i`` to send a single query and exit. Useful for scripting or quick lookups:

.. code-block:: bash

   # Text query
   pantheon cli -i "Summarize the methods section of this paper" @report.pdf

   # Pipe output to a file
   pantheon cli -i "Write a bash script to rename files by date" > rename.sh

   # Non-interactive with a specific model
   pantheon cli -i "What is the GC content of this sequence?" --model openai/gpt-4o

The ``@path`` syntax attaches a file as context. Tab completion works for file paths.

Key Shortcuts
-------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Key
     - Action
   * - **Enter**
     - Send the current message
   * - **Ctrl+C**
     - Cancel the currently streaming generation
   * - **Ctrl+D**
     - Exit Pantheon CLI
   * - **Esc**
     - Cancel generation (alternative to Ctrl+C)
   * - **Ctrl+T**
     - Toggle between compact and verbose display modes
   * - **Alt+Enter**
     - Insert a newline (multi-line input without sending)
   * - **Up / Down Arrow**
     - Navigate input history

Next Steps
----------

- :doc:`commands` — complete slash command and option reference
- :doc:`session-management` — listing, resuming, and exporting sessions
- :doc:`mcp-servers` — adding MCP tool servers to your CLI session
- :doc:`file-viewer` — keyboard navigation in the ``/view`` file viewer
