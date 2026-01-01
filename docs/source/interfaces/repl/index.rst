REPL Interface
==============

The REPL (Read-Eval-Print Loop) provides a feature-rich command-line interface for interacting with Pantheon agents and teams.

Quick Start
-----------

Start the REPL with default settings:

.. code-block:: bash

   python -m pantheon.repl

You'll see a welcome message and prompt. Type your message and press Enter to chat with the agent.

.. code-block:: text

   ╭─ Pantheon REPL ─╮
   │ Type /help for commands │
   ╰──────────────────────────╯

   > Hello! What can you help me with?

Starting Options
----------------

.. code-block:: bash

   # Use a specific team template
   python -m pantheon.repl --template data_research_team

   # Set memory directory
   python -m pantheon.repl --memory-dir ./my_chats

   # Resume a previous chat
   python -m pantheon.repl --chat-id abc123

   # Quiet mode (less output)
   python -m pantheon.repl --quiet

Key Features
------------

Syntax Highlighting
~~~~~~~~~~~~~~~~~~~

Code in responses is automatically highlighted:

.. code-block:: text

   > Write a Python hello world

   Here's the code:

   ```python
   print("Hello, World!")
   ```

File Viewer
~~~~~~~~~~~

View files with syntax highlighting using ``/view``:

.. code-block:: text

   > /view src/main.py

This opens a full-screen viewer with:

- Line numbers
- Syntax highlighting
- Vim-style navigation (j/k, g/G)
- Page navigation (Space, Ctrl-F/B)

See :doc:`file-viewer` for details.

Command History
~~~~~~~~~~~~~~~

- Use arrow keys to navigate previous commands
- History persists across sessions
- Stored in ``~/.pantheon/cli_history``

Auto-Completion
~~~~~~~~~~~~~~~

- Tab completion for commands
- File path completion

Multi-line Input
~~~~~~~~~~~~~~~~

For multi-line messages, use triple backticks:

.. code-block:: text

   > ```
   This is a
   multi-line
   message
   ```

Common Commands
---------------

.. list-table::
   :header-rows: 1

   * - Command
     - Description
   * - ``/help``
     - Show available commands
   * - ``/view <file>``
     - View file with syntax highlighting
   * - ``/clear``
     - Clear conversation context
   * - ``/compress``
     - Compress conversation to save tokens
   * - ``/exit``
     - Exit the REPL

See :doc:`commands` for the complete reference.

Configuration
-------------

REPL settings are stored in ``.pantheon/settings.json``:

.. code-block:: json

   {
     "repl": {
       "quiet": false,
       "default_template": "default",
       "log_level": "ERROR"
     }
   }

See :doc:`/configuration/settings` for all options.

Next Steps
----------

- :doc:`commands` - Full command reference
- :doc:`file-viewer` - File viewer features
- :doc:`advanced` - Custom handlers and extensions

.. toctree::
   :hidden:
   :maxdepth: 1

   commands
   file-viewer
   advanced
