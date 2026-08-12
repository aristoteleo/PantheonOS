CLI (REPL)
==========

The Pantheon CLI provides a full-featured terminal interface for working with agents and
teams. It supports streaming responses, rich syntax highlighting, slash commands, session
persistence, and all the same teams and toolsets as the web app.

.. code-block:: bash

   pantheon cli

You are dropped into an interactive prompt immediately. No additional setup is required
beyond having an API key configured.

Feature Overview
----------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Feature
     - Details
   * - **Streaming responses**
     - Tokens stream to the terminal in real time; no waiting for the full response.
   * - **Syntax highlighting**
     - Code blocks in responses are automatically highlighted by language. File
       viewer (``/view``) provides full syntax highlighting with line numbers.
   * - **Slash commands**
     - ~25 built-in commands covering sessions, agents, models, files, MCP servers,
       and display modes. See :doc:`commands`.
   * - **Session persistence & resume**
     - Every conversation is saved automatically. Resume any session by name, ID,
       or ``last`` on startup or mid-session.
   * - **File viewer**
     - ``/view <path>`` opens a full-screen file viewer with Vim-style navigation
       (j/k, g/G, Space/Ctrl-B, q to exit).
   * - **Shell pass-through**
     - Prefix a command with ``!`` to run it directly in the shell without the LLM
       (e.g., ``!git status``, ``!ls -la``).
   * - **MCP server management**
     - Start, stop, restart, add, and remove MCP tool servers live without restarting
       the CLI session.
   * - **Model switching**
     - ``/model <name>`` changes the active model mid-session; change persists across
       restarts.
   * - **Multi-agent teams**
     - All team templates available in the web app work identically in the CLI.
       Switch agents mid-conversation with ``/agent``.
   * - **Context compression**
     - ``/compress`` triggers context summarization when approaching the model's
       context limit.
   * - **Speech input**
     - Not available in the CLI. Use the web or desktop app for speech-to-text input.

Command-Line Options
--------------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Option
     - Description
   * - ``--template <name>``
     - Load a specific team template on startup.
   * - ``--resume <id\|name\|last>``
     - Resume a saved session immediately on startup.
   * - ``-i <message>``
     - Non-interactive mode: send one message and exit. Useful for scripting.
   * - ``--model <name\|tag>``
     - Override the model for all agents in this session.
   * - ``--quiet``
     - Suppress decorative output; useful when piping CLI output to other tools.

See :doc:`commands` for the complete options reference.

When to Use the CLI
-------------------

Prefer the Pantheon CLI when:

- Working on a remote server over SSH without a browser.
- Scripting or automating one-shot queries with ``-i``.
- Integrating Pantheon into shell pipelines or Makefiles.
- Preferring a minimal, keyboard-driven interface.
- Running on a headless machine or inside a container.
- You want the fastest possible startup time (the CLI starts in under a second).

Use the web or desktop app when you need Live View visualizations, the Fleet panel,
the integrated PTY terminal, or the Gateway configuration UI.

.. toctree::
   :hidden:
   :maxdepth: 1

   quickstart
   commands
   session-management
   file-viewer
   mcp-servers
   advanced
