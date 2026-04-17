ChatRoom Quick Start
====================

Get the web UI running in under a minute.

Prerequisites
-------------

1. Pantheon installed: ``pip install pantheon-agents``
2. API key configured: ``export OPENAI_API_KEY="..."``

Step 1: Start ChatRoom
----------------------

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

This will:

- Start a local NATS messaging server
- Launch the ChatRoom backend
- Open the web UI in your browser (auto-connected)

You'll see output like:

.. code-block:: text

   [STARTUP] Auto-starting local NATS server...
   ✓ NATS server started successfully
     TCP URL: nats://localhost:4222
     WebSocket URL: ws://127.0.0.1:8080
   [STARTUP] Worker is ready, opening browser...
   [FRONTEND] ✓ Browser opened successfully

Step 2: Start Chatting
----------------------

The browser opens automatically with the connection pre-configured. Type your message and press Enter.

Using Team Templates
--------------------

ChatRoom uses team templates from ``.pantheon/teams/``. To use a specific template:

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui --template data_research_team

Available options:

.. code-block:: bash

   # List available templates
   ls .pantheon/teams/

   # Use a template
   pantheon ui --auto-start-nats --auto-ui --template <template_name>

Creating Your First Template
----------------------------

Create a file ``.pantheon/teams/my_team.md``:

.. code-block:: markdown

   ---
   name: My Team
   icon: 🤖
   agents:
     - name: assistant
       instructions: You are a helpful assistant.
     - name: coder
       instructions: You are a coding expert.
       toolsets:
         - python_interpreter
         - file_manager
   ---

   # My Custom Team

   This team helps with general tasks and coding.

Then start with your template:

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui --template my_team

Connecting to a Remote NATS Server
-----------------------------------

If you have a remote NATS server, you can connect directly without auto-starting a local one:

.. code-block:: bash

   pantheon ui --nats-servers "wss://your-server.com/nats"

In this mode, you need to open the web UI manually and provide the service ID displayed in the terminal.

Troubleshooting
---------------

**Browser Didn't Open**

- Check the terminal for the connection URL and open it manually
- Ensure ``--auto-start-nats`` is used together with ``--auto-ui``

**Running in WSL**

- ``--auto-ui`` now tries to open your Windows default browser directly when Pantheon runs inside WSL
- If automatic launch still fails, copy the ``Full Connection URL`` from the terminal into a Windows browser manually
- If you use a custom frontend URL, keep ``--auto-start-nats`` enabled so the generated WebSocket connection stays local to your WSL session

**NATS Server Failed to Start**

- Check if another NATS instance is already running on port 4222 or 8080
- Try ``--log-level DEBUG`` for more details

**Agent Not Responding**

- Verify your API key is set correctly
- Check the terminal for error messages

**Template Not Found**

- Ensure the template file exists in ``.pantheon/teams/``
- Check the file name matches (without ``.md`` extension)

Next Steps
----------

- :doc:`web-interface` - Explore UI features
- :doc:`/configuration/templates/teams` - Create custom team templates
