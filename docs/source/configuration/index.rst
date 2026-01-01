Configuration
=============

Pantheon uses a unified configuration system shared by all interfaces (REPL, UI, and API).

Overview
--------

Configuration is stored in the ``.pantheon/`` directory:

.. code-block:: text

   .pantheon/
   ├── settings.json    # Main configuration
   ├── mcp.json         # MCP server definitions
   ├── agents/          # Agent templates
   ├── teams/           # Team templates
   ├── prompts/         # Reusable prompt snippets
   ├── memory/          # Conversation storage
   └── logs/            # Application logs

Configuration Priority
----------------------

Settings are loaded in this order (highest priority first):

1. **Command-line arguments**
2. **Environment variables**
3. **Project config** (``./.pantheon/``)
4. **User global config** (``~/.pantheon/``)
5. **Package defaults**

This means project-level settings override user-level settings, and CLI arguments override everything.

Quick Setup
-----------

**Create project configuration:**

.. code-block:: bash

   mkdir -p .pantheon/agents .pantheon/teams

**Set API key:**

.. code-block:: bash

   export OPENAI_API_KEY="your-key"

Or in ``.pantheon/settings.json``:

.. code-block:: json

   {
     "api_keys": {
       "openai": "your-key"
     }
   }

**Create an agent template:**

Create ``.pantheon/agents/my_assistant.md``:

.. code-block:: markdown

   ---
   name: My Assistant
   model: openai/gpt-4o-mini
   icon: 🤖
   ---

   You are a helpful assistant.

**Use in any interface:**

.. code-block:: bash

   # REPL
   python -m pantheon.repl --template my_assistant

   # ChatRoom
   python -m pantheon.chatroom --template my_assistant

.. code-block:: python

   # API
   from pantheon.factory import load_agent
   agent = load_agent("my_assistant")

Shared Features
---------------

All interfaces use the same:

- **Agent templates** from ``.pantheon/agents/``
- **Team templates** from ``.pantheon/teams/``
- **Model configuration** from ``settings.json``
- **MCP servers** from ``mcp.json``

This means you configure once and use everywhere.

Next Steps
----------

- :doc:`settings` - Full settings.json reference
- :doc:`templates/index` - Creating agent and team templates
- :doc:`mcp` - MCP server configuration
- :doc:`models` - Model selection and fallbacks

.. toctree::
   :hidden:
   :maxdepth: 2

   settings
   templates/index
   mcp
   models
