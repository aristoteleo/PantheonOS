Interfaces
==========

Pantheon provides three ways to interact with AI agents. Each interface has its strengths, but they all share the same underlying configuration and capabilities.

Overview
--------

.. list-table::
   :header-rows: 1
   :widths: 20 30 25 25

   * - Interface
     - Description
     - Best For
     - Start Command
   * - **REPL**
     - Command-line interface with rich features
     - Developers, quick experiments
     - ``pantheon cli``
   * - **Web UI**
     - Browser-based visual interface
     - Demos, daily use
     - ``pantheon ui --auto-start-nats --auto-ui``
   * - **Python API**
     - Full programmatic control
     - Integrations, custom apps
     - ``from pantheon.agent import Agent``

Shared Features
---------------

All three interfaces support:

**Configuration**

- Read from ``.pantheon/settings.json``
- Use agent/team templates from ``.pantheon/agents/`` and ``.pantheon/teams/``
- Connect to MCP servers configured in ``.pantheon/mcp.json``

**Capabilities**

- All toolsets (file operations, code execution, web search, etc.)
- PantheonTeam in team mode and agent mode
- Memory and conversation persistence
- Streaming responses

**Models**

- Same model configuration applies to all interfaces
- Fallback chains work identically

Quick Start
-----------

REPL
~~~~

.. code-block:: bash

   pantheon cli

Features:

- Syntax highlighting
- ``/view <file>`` full-screen file viewer
- Command history
- Auto-completion

Web UI
~~~~~~

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

Starts a local NATS server and opens the web UI in your browser automatically.

Features:

- Visual interface
- File uploads
- Session management
- Auto-connect

Python API
~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets import FileManagerToolSet

   agent = Agent(
       name="assistant",
       instructions="You are helpful."
   )

   # Add toolsets at runtime
   await agent.toolset(FileManagerToolSet("files"))

   # Single query
   response = await agent.run("Hello!")

   # Interactive chat
   await agent.chat()

Features:

- Full control
- Custom logic
- Easy integration

Choosing an Interface
---------------------

**Use REPL when:**

- You want to experiment quickly
- You're comfortable with command line
- You need advanced features like file viewing

**Use Web UI when:**

- You want a visual experience
- You're demoing to others
- You prefer browser-based tools

**Use Python API when:**

- You're building a custom application
- You need to integrate with other code
- You want maximum flexibility

