First Steps: Choose Your Interface
===================================

Pantheon provides three ways to interact with AI agents. Choose the one that fits your needs.

.. contents:: On this page
   :local:
   :depth: 2

REPL (Command Line)
-------------------

**Best for:** Quick experiments, developers, command-line lovers

The REPL is the fastest way to start using Pantheon. It provides a rich command-line interface with syntax highlighting, file viewing, and more.

.. code-block:: bash

   # Start with default settings
   pantheon cli

   # Or load a team template file (factory templates are copied into
   # .pantheon/teams/ on first run)
   pantheon cli --template .pantheon/teams/single_cell_team.md

**Features:**

- Syntax highlighting for code
- ``/view <file>`` - Full-screen file viewer
- ``/compress`` - Compress long conversations
- Auto-completion and history

➡️ Learn more: :doc:`/interfaces/repl/index`

Web UI (ChatRoom)
-----------------

**Best for:** Demos, non-technical users, visual workflow

The ChatRoom provides a web-based interface accessible from any browser.

.. code-block:: bash

   # Start the multi-agent chatroom
   pantheon ui --auto-start-nats --auto-ui

**Features:**

- Clean visual interface
- File upload support
- Session management
- Multi-user support

➡️ Learn more: :doc:`/interfaces/ui/index`

Python API
----------

**Best for:** Developers, integrations, custom applications

Full programmatic control over agents and teams.

.. code-block:: python

   import asyncio
   from pantheon.agent import Agent

   async def main():
       agent = Agent(
           name="assistant",
           instructions="You are a helpful assistant."
       )

       # Single query
       response = await agent.run("Hello!")
       print(response.content)

       # Or interactive chat
       await agent.chat()

   asyncio.run(main())

**Features:**

- Full control over agent behavior
- Easy integration with existing code
- Async/await support
- Custom toolsets

➡️ Learn more: :doc:`/interfaces/api/index`

Shared Configuration
--------------------

All three interfaces share the same configuration system through the ``.pantheon/`` directory:

.. code-block:: text

   .pantheon/
   ├── settings.json    # Global settings (models, API keys, etc.)
   ├── mcp.json         # MCP server configuration
   ├── agents/          # Agent templates (markdown files)
   ├── teams/           # Team templates
   └── prompts/         # Reusable prompt snippets

This means:

- Configure once, use everywhere
- Same agent templates work in REPL, UI, and API
- Switch between interfaces freely

➡️ Learn more: :doc:`/configuration/index`

Quick Comparison
----------------

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Feature
     - REPL
     - Web UI
     - Python API
   * - Setup time
     - Instant
     - 1 minute
     - 5 minutes
   * - Best for
     - Power users
     - Everyone
     - Developers
   * - Customization
     - Medium
     - Low
     - Full
   * - Learning curve
     - Easy
     - Very easy
     - Medium

Next Steps
----------

Ready to build your first agent? Continue to :doc:`5min-tutorial`.
