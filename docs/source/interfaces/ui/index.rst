Web / Desktop App
=================

The Pantheon web and desktop app is the full graphical interface for PantheonOS. It provides every
capability of the platform in a visual, multi-user environment accessible from a browser or the
native desktop application.

Feature Overview
----------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Feature
     - Description
   * - **Chat Interface**
     - Stream messages to one or more agents; colored agent names for multi-agent attribution;
       file and image attachments; speech-to-text via microphone.
   * - **Projects & Workspaces**
     - Organize work into isolated projects, each with its own file workspace, memory store,
       and running endpoint subprocess.
   * - **Team Management**
     - Browse and apply team templates; switch the active agent mid-conversation; configure
       per-agent model overrides interactively.
   * - **Model Selection**
     - Full model browser with OpenRouter catalog (300+ models), local Ollama models, saved
       favorites, and inline API-key management.
   * - **Store & Skills**
     - Install agent skills, team templates, and toolset configurations from the Pantheon Store
       or load local skills from ``.pantheon/skills/``.
   * - **Pantheon Fleet**
     - Connect and control multiple machines from a single UI session; mint join tokens;
       run commands on remote nodes.
   * - **Live View**
     - Open interactive in-browser visualizations (genome browsers, 3D structure viewers,
       spatial maps, network graphs) alongside the chat — no external tool required.
   * - **Integrated Terminal**
     - Full PTY terminal backed by a real shell process on the server; output streams live;
       sessions survive reconnects.
   * - **Messaging Gateway**
     - Connect agents to external messaging platforms (Discord, WeChat) so users can
       interact through third-party channels.
   * - **App Backends**
     - Each project runs its own endpoint subprocess; attach to remote endpoints or switch
       between multiple running backends from the UI.

Starting the App
----------------

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

This single command starts the full stack:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Effect
   * - ``--auto-start-nats``
     - Starts a local NATS server (WebSocket on port 8080, TCP on 4222) that the
       frontend and backend use to communicate. Required for local usage without an
       external NATS cluster.
   * - ``--auto-ui``
     - Opens the Pantheon web UI in your default browser with the connection URL
       pre-configured so you can start chatting immediately. On WSL, Pantheon
       attempts to launch the Windows default browser.

Additional options:

.. code-block:: bash

   # Use a specific team template
   pantheon ui --auto-start-nats --auto-ui --template data_research_team

   # Connect to a remote NATS server
   pantheon ui --nats-servers "wss://your-server.example.com/nats"

   # Stable service ID (useful for reconnects from the same URL)
   pantheon ui --auto-start-nats --auto-ui --id-hash mylab

   # Debug logging
   pantheon ui --auto-start-nats --auto-ui --log-level DEBUG

See the full option reference in :doc:`quickstart`.

Web vs Desktop
--------------

The same backend powers both delivery modes:

- **Web app** — open ``http://localhost:8080`` (or the URL shown in the terminal) in any
  modern browser. Supports multiple simultaneous users connecting to the same backend.
- **Desktop app** — a downloadable installer that embeds the same web UI in an Electron
  shell. No separate browser session required. The desktop app connects to the same
  local or remote backend using the standard NATS transport.

There is no functional difference between the two; use whichever fits your workflow. The
desktop app is convenient for single-user local setups; the web app is preferred for
shared lab or team deployments.

.. note::

   The desktop installer is available from the PantheonOS releases page. On first launch it
   prompts for a NATS server URL or can start one automatically.

Next Steps
----------

- :doc:`quickstart` — step-by-step first-use guide
- :doc:`core-features` — chat, sessions, context management, and keyboard shortcuts
- :doc:`projects` — organizing workspaces and per-project isolation
- :doc:`teams-agents` — team templates and agent configuration
- :doc:`models-providers` — model browser, OpenRouter, and Ollama
- :doc:`store-skills` — installing skills and browsing the Pantheon Store
- :doc:`fleet` — controlling multiple machines from the UI
- :doc:`live-view` — interactive in-browser visualizations
- :doc:`terminal` — integrated PTY terminal
- :doc:`gateway` — connecting agents to Discord, WeChat, and other platforms

.. toctree::
   :hidden:
   :maxdepth: 1

   quickstart
   core-features
   projects
   teams-agents
   models-providers
   store-skills
   fleet
   live-view
   terminal
   gateway
   advanced
