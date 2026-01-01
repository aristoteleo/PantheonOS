Web UI (ChatRoom)
=================

The ChatRoom provides a web-based interface for interacting with Pantheon agents and teams.

Overview
--------

ChatRoom is a service that exposes your agents through a web interface. It connects to the Pantheon UI at https://pantheon-ui.vercel.app/ for a rich chat experience.

Quick Start
-----------

**1. Start the ChatRoom server:**

.. code-block:: bash

   python -m pantheon.chatroom

**2. Copy the service ID** from the output:

.. code-block:: text

   ChatRoom started!
   Service ID: abc123-def456-...
   Connect at: https://pantheon-ui.vercel.app/

**3. Open the UI** in your browser and paste the service ID.

**4. Start chatting!**

Features
--------

- **Visual Interface**: Clean, modern chat UI
- **File Uploads**: Attach files to conversations
- **Multi-User**: Multiple users can connect simultaneously
- **Session Persistence**: Conversations are saved and can be resumed
- **Team Support**: Use any team configuration

Configuration
-------------

ChatRoom reads configuration from ``.pantheon/``:

- ``settings.json`` - General settings
- ``teams/*.md`` - Team templates
- ``mcp.json`` - MCP server configuration

.. code-block:: bash

   # Use a specific team template
   python -m pantheon.chatroom --template data_research_team

   # Set memory directory
   python -m pantheon.chatroom --memory-dir ./chats

Architecture
------------

.. code-block:: text

   ┌─────────────────┐     ┌─────────────────┐
   │  Web Browser    │────▶│  Pantheon UI    │
   │  (User)         │     │  (vercel.app)   │
   └─────────────────┘     └────────┬────────┘
                                    │ WebSocket
                                    ▼
                           ┌─────────────────┐
                           │  ChatRoom       │
                           │  (Your server)  │
                           └────────┬────────┘
                                    │
                           ┌────────┴────────┐
                           ▼                 ▼
                    ┌──────────┐      ┌──────────┐
                    │  Agent   │      │ Toolsets │
                    └──────────┘      └──────────┘

The ChatRoom runs on your machine, connecting to:

- The web UI via WebSocket
- Your configured agents and toolsets
- MCP servers (if configured)

Next Steps
----------

- :doc:`quickstart` - Step-by-step setup
- :doc:`web-interface` - UI features
- :doc:`advanced` - Programmatic usage and customization

.. toctree::
   :hidden:
   :maxdepth: 1

   quickstart
   web-interface
   advanced
