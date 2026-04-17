Web UI (ChatRoom)
=================

The ChatRoom provides a web-based interface for interacting with Pantheon agents and teams.

Overview
--------

ChatRoom is a service that exposes your agents through a web interface. It starts a local NATS messaging server and automatically opens the Pantheon UI in your browser.

Quick Start
-----------

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

This single command will:

1. Start a local NATS server (for messaging between frontend and backend)
2. Start the ChatRoom backend with your configured agents
3. Automatically open the web UI in your browser and connect

That's it — start chatting!

Features
--------

- **Visual Interface**: Clean, modern chat UI
- **File Uploads**: Attach files to conversations
- **Multi-User**: Multiple users can connect simultaneously
- **Session Persistence**: Conversations are saved and can be resumed
- **Team Support**: Use any team configuration
- **Auto-Connect**: ``--auto-ui`` opens the browser with connection pre-configured
- **WSL Friendly**: On WSL, ``--auto-ui`` attempts to launch your Windows default browser instead of relying on Linux desktop browser handlers

Command Reference
-----------------

.. code-block:: bash

   pantheon ui [OPTIONS]

**Core Options:**

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Option
     - Default
     - Description
   * - ``--auto-start-nats``
     - ``False``
     - Start a local NATS server automatically. Required for local usage.
   * - ``--auto-ui``
     - ``False``
     - Open browser with auto-connect config. Requires ``--auto-start-nats``. Can optionally specify a custom frontend URL (e.g., ``--auto-ui "http://localhost:5173"``).
   * - ``--template``
     -
     - Team template name from ``.pantheon/teams/``.
   * - ``--nats-servers``
     -
     - NATS server URL(s). Supports WebSocket (``wss://``) and TCP (``nats://``). Multiple servers separated by ``|``.
   * - ``--endpoint-mode``
     - ``embedded``
     - How to start the endpoint: ``embedded`` (same event loop) or ``process`` (subprocess).

**Advanced Options:**

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Option
     - Default
     - Description
   * - ``--service-name``
     - from settings
     - Service name for the ChatRoom instance.
   * - ``--memory-dir``
     - from settings
     - Directory to store conversation memory.
   * - ``--workspace-path``
     - from settings
     - Workspace directory for the endpoint.
   * - ``--log-level``
     - ``INFO``
     - Log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
   * - ``--id-hash``
     -
     - Hash string for stable service ID (e.g., ``"alice"``). Generates UUID if not provided.
   * - ``--endpoint-service-id``
     -
     - Connect to an existing remote endpoint instead of starting a new one.
   * - ``--speech-to-text-model``
     - from settings
     - Model for speech-to-text transcription.

**Examples:**

.. code-block:: bash

   # Quick local start (recommended)
   pantheon ui --auto-start-nats --auto-ui

   # Use a specific team template
   pantheon ui --auto-start-nats --auto-ui --template data_research_team

   # Custom frontend URL (for local development)
   pantheon ui --auto-start-nats --auto-ui "http://localhost:5173"

   # Connect to a remote NATS server
   pantheon ui --nats-servers "wss://your-server.com/nats"

   # Set a stable service ID
   pantheon ui --auto-start-nats --auto-ui --id-hash alice

   # Debug mode
   pantheon ui --auto-start-nats --auto-ui --log-level DEBUG

Configuration
-------------

ChatRoom reads configuration from ``.pantheon/``:

- ``settings.json`` - General settings
- ``teams/*.md`` - Team templates
- ``mcp.json`` - MCP server configuration

Architecture
------------

.. code-block:: text

   ┌─────────────────┐     ┌─────────────────┐
   │  Web Browser    │◀───▶│  NATS Server    │
   │  (Pantheon UI)  │ WS  │  (local:8080)   │
   └─────────────────┘     └────────┬────────┘
                                    │ TCP
                           ┌────────┴────────┐
                           │  ChatRoom       │
                           │  (Backend)      │
                           └────────┬────────┘
                                    │
                           ┌────────┴────────┐
                           ▼                 ▼
                    ┌──────────┐      ┌──────────┐
                    │  Agents  │      │ Toolsets │
                    └──────────┘      └──────────┘

With ``--auto-start-nats``, a local NATS server is started that provides:

- **WebSocket** (``ws://127.0.0.1:8080``) for browser connections
- **TCP** (``nats://localhost:4222``) for backend communication

The ChatRoom backend connects to NATS and manages your agents, toolsets, and MCP servers.

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
