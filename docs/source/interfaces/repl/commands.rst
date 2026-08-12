Command Reference
=================

Complete reference for the Pantheon CLI — startup options, slash commands, file input
syntax, and keyboard shortcuts.

Starting Pantheon CLI
---------------------

.. code-block:: bash

   pantheon cli [OPTIONS] [--] [MESSAGE]

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Option
     - Default
     - Description
   * - ``--template <name>``
     -
     - Team template to load. Uses a built-in template name or a filename from
       ``.pantheon/teams/`` (without the ``.md`` extension).
   * - ``--memory-dir <path>``
     - ``.pantheon/memory``
     - Directory where session files are stored and read from.
   * - ``--workspace <path>``
     - current directory
     - Workspace root that agents operate in when workspace mode is active.
   * - ``--chat-id <id>``
     -
     - Resume a specific session by its exact ID.
   * - ``--resume <id\|name\|last>``
     -
     - Resume a session by partial name, full ID, or the keyword ``last`` to
       resume the most recently active session.
   * - ``-r <id\|name\|last>``
     -
     - Shorthand for ``--resume``.
   * - ``--log-level <level>``
     - ``ERROR``
     - Logging verbosity: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``.
   * - ``--quiet``
     - ``False``
     - Suppress status messages and decorative output. Useful for scripted use.
   * - ``--resync``
     - ``False``
     - Force a re-index of the workspace knowledge base on startup.
   * - ``-i <message>``
     -
     - Send a single non-interactive query and exit. Accepts ``@path`` file
       attachments. Output is printed to stdout.
   * - ``--model <name\|tag>``
     -
     - Override the model for all agents in this session. Accepts a full model
       identifier (e.g., ``openai/gpt-4o``) or a quality tag (``high``,
       ``normal``, ``fast``).

Slash Commands — Complete Reference
-------------------------------------

Session & Navigation
~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Description
   * - ``/help``
     - Show all available slash commands with brief descriptions.
   * - ``/status``
     - Show current session information: active model, team template name, chat ID,
       and approximate token count for the current context.
   * - ``/new``
     - Start a new chat session. The current session is saved automatically. The
       new session starts with an empty conversation.
   * - ``/clear``
     - Clear the current conversation history. Prompts for confirmation before
       deleting. The session ID is preserved but all turns are removed.
   * - ``/exit``, ``/quit``, ``/q``
     - Exit Pantheon CLI. The current session is saved before exiting.

Chat History
~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Description
   * - ``/history``
     - Show the input history for the current CLI session (commands and messages
       sent since startup). Not the same as conversation history.
   * - ``/list`` (or ``/chats``)
     - List all saved chat sessions. Displays session ID, name, creation date,
       last modified date, and message count.
   * - ``/resume [id\|name\|last]``
     - Resume a previous chat. Accepts a full session ID, a partial session name
       (case-insensitive substring match), or ``last`` for the most recent session.
       If no argument is provided, shows an interactive session picker.
   * - ``/save [file]``
     - Save the current conversation to a JSON file. If no filename is provided,
       saves to ``<session-id>.json`` in the current directory.
   * - ``/load <file>``
     - Load a conversation from a JSON file exported by ``/save``. The loaded
       conversation replaces the current context; all history from the file is
       available to the agent.
   * - ``/revert [index]``
     - Revert the conversation to an earlier user turn. If ``index`` is provided,
       reverts to that turn number (0-indexed). If omitted, shows a numbered list
       of user turns to pick from.

Agents & Teams
~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Description
   * - ``/agents``
     - List all agents in the current team: name, role, model, and available
       toolsets.
   * - ``/agent <name\|n>``
     - Switch the active agent. Accepts an agent name (partial match) or a
       1-based agent number from the ``/agents`` list.
   * - ``/team [list\|id\|path]``
     - With no argument: show the current team name and template path. With
       ``list``: show available templates. With a template ID or file path:
       switch to that template immediately.

Models & Keys
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Description
   * - ``/model [name\|tag]``
     - With no argument: show the current model. With a model name or quality
       tag: switch the model for the active agent. Model changes are persisted
       to the team template file and survive restarts.
   * - ``/keys [show\|set <provider>]``
     - With no argument or ``show``: display configured provider API keys
       (values are masked). With ``set <provider>``: prompt for and store a new
       key for the given provider name.
   * - ``/tokens``
     - Show a token usage breakdown for the current conversation: total tokens,
       tokens per turn, estimated cost at current model pricing.

Context & Display
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Description
   * - ``/compress``
     - Immediately trigger context compression. The agent summarizes the earlier
       conversation to reduce token usage. Use when approaching the context limit.
   * - ``/verbose``, ``/v``
     - Switch to verbose display mode. All tool calls, tool results, and agent
       reasoning steps are shown in full.
   * - ``/compact``, ``/c``
     - Switch to compact display mode. Tool calls and results are collapsed to
       one-line summaries. This is the default mode.

Files
~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Description
   * - ``/view <path>``
     - Open the file at ``<path>`` in the full-screen file viewer with syntax
       highlighting, line numbers, and Vim-style navigation. Press ``q`` or
       ``Esc`` to exit the viewer.
   * - ``/edit [path]``
     - Open a file in ``$EDITOR`` (falls back to ``nvim``, then ``vim``). After
       you save and close the editor, you are returned to the REPL prompt.

MCP Servers
~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Command
     - Description
   * - ``/mcp list``
     - List all configured MCP servers, their start commands, and current status
       (running / stopped / error).
   * - ``/mcp start <name>``
     - Start a stopped MCP server. The server's tools become available to agents
       immediately after startup.
   * - ``/mcp stop <name>``
     - Stop a running MCP server. Its tools are removed from the agent's toolset
       without requiring a restart.
   * - ``/mcp restart <name>``
     - Stop and restart an MCP server. Useful after updating the server binary or
       when a server has entered an error state.
   * - ``/mcp add <name> <cmd>``
     - Add a new MCP server for this session. ``<cmd>`` is the full shell command
       to start the server (e.g., ``npx @modelcontextprotocol/server-filesystem``).
       The server starts immediately and its tools become available.
   * - ``/mcp remove <name>``
     - Remove an MCP server from the current session. The server is stopped and
       its tools are unregistered. Does not modify ``mcp.json``.

Shell Pass-Through
~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Syntax
     - Description
   * - ``!<cmd>``
     - Run a shell command directly without involving the LLM. The command runs
       in a subprocess using the current shell. Output is printed to the terminal.
       Example: ``!ls -la``, ``!git status``, ``!python train.py``.

Input Features
--------------

**File attachment with** ``@``

Attach a file as context by typing ``@<path>`` anywhere in your message:

.. code-block:: text

   > Summarize the key findings in @results/analysis_report.pdf

Tab completion is supported for ``@path`` attachments — press Tab after ``@`` to
autocomplete file paths relative to the current workspace.

**Image attachment with** ``@image:``

Attach an image for vision models:

.. code-block:: text

   > What cell types are shown in @image:figures/umap_by_celltype.png

Images are base64-encoded and sent as vision input. Requires a model that supports
image input (look for the ``vision`` capability tag in ``/model``).

**Multi-line input**

Press **Alt+Enter** or **Ctrl+J** to insert a newline without sending. Press **Enter**
on its own to submit the complete multi-line message:

.. code-block:: text

   > Please review this function:
     [Alt+Enter]
     def calculate_gc(seq):
         return (seq.count('G') + seq.count('C')) / len(seq)
     [Enter to send]

**History navigation**

Press **Up Arrow** to scroll backward through previous messages and commands you have
entered in this session. **Down Arrow** scrolls forward. History persists across
sessions and is stored in ``~/.pantheon/cli_history``.

Keyboard Shortcuts
------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Key
     - Action
   * - **Enter**
     - Send the current message to the active agent
   * - **Shift+Enter**
     - Insert a newline (same as Alt+Enter in most terminals)
   * - **Alt+Enter**
     - Insert a newline without sending (for multi-line messages)
   * - **Ctrl+C**
     - Cancel the currently streaming generation; returns to the prompt
   * - **Ctrl+D**
     - Exit Pantheon CLI (equivalent to ``/exit``)
   * - **Ctrl+T**
     - Toggle between compact and verbose display modes
   * - **Esc**
     - Cancel generation (alternative to Ctrl+C)
   * - **Up Arrow**
     - Navigate to the previous message/command in input history
   * - **Down Arrow**
     - Navigate to the next message/command in input history
   * - **Tab**
     - Autocomplete slash commands and ``@path`` file references
