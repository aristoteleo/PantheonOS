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
     - ``python -m pantheon.repl``
   * - **Web UI**
     - Browser-based visual interface
     - Demos, daily use
     - ``python -m pantheon.chatroom``
   * - **Python API**
     - Full programmatic control
     - Integrations, custom apps
     - ``from pantheon import Agent``

Shared Features
---------------

All three interfaces support:

**Configuration**

- Read from ``.pantheon/settings.json``
- Use agent/team templates from ``.pantheon/agents/`` and ``.pantheon/teams/``
- Connect to MCP servers configured in ``.pantheon/mcp.json``

**Capabilities**

- All toolsets (file operations, code execution, web search, etc.)
- All team patterns (Pantheon, Swarm, Sequential, MoA)
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

   python -m pantheon.repl

Features:

- Syntax highlighting
- ``/view <file>`` full-screen file viewer
- Command history
- Auto-completion

Web UI
~~~~~~

.. code-block:: bash

   python -m pantheon.chatroom

Then open https://pantheon-ui.vercel.app/ and connect with the displayed service ID.

Features:

- Visual interface
- File uploads
- Session management

Python API
~~~~~~~~~~

.. code-block:: python

   from pantheon import Agent
   from pantheon.toolsets import FileManagerToolSet

   agent = Agent(
       name="assistant",
       instructions="You are helpful.",
       model="gpt-4o-mini",
       tools=[FileManagerToolSet()]
   )

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

.. toctree::
   :hidden:
   :maxdepth: 2

   repl/index
   ui/index
   api/index
