MCP Servers
===========

MCP (Model Context Protocol) servers extend your agents with additional tools — databases,
APIs, file systems, and specialized capabilities. The Pantheon CLI lets you manage MCP
servers live without restarting the session.

Overview
--------

MCP is an open protocol for exposing tools to LLM agents. An MCP server is a process that
implements the protocol and responds to tool calls from the agent. Once an MCP server is
running, its tools appear alongside Pantheon's built-in toolsets — the agent can call them
interchangeably.

Pantheon supports any spec-compliant MCP server, including:

- The official ``@modelcontextprotocol`` reference servers (filesystem, GitHub, Slack, etc.)
- Third-party servers from the MCP ecosystem
- Custom servers you build with any MCP SDK

Configuration
-------------

MCP servers are defined in ``.pantheon/mcp.json``. Each entry specifies a name, the
command to start the server, optional arguments, and environment variables:

.. code-block:: json

   {
     "servers": {
       "filesystem": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/data"],
         "env": {}
       },
       "github": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-github"],
         "env": {
           "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
         }
       },
       "my-db-server": {
         "command": "python",
         "args": ["-m", "my_mcp_server"],
         "env": {
           "DB_URL": "postgresql://localhost/mydb"
         }
       }
     }
   }

See :doc:`/configuration/mcp` for the complete configuration format including transport
options and startup timeouts.

Listing Servers
---------------

.. code-block:: text

   > /mcp list

   Name           Command                                     Status
   ─────────────  ──────────────────────────────────────────  ───────
   filesystem     npx @modelcontextprotocol/server-filesystem  Running
   github         npx @modelcontextprotocol/server-github      Stopped
   my-db-server   python -m my_mcp_server                      Error

The status column shows:

- **Running** — server is connected and its tools are available.
- **Stopped** — server is configured but not currently running.
- **Error** — server failed to start or crashed. See troubleshooting below.

Starting & Stopping Servers
----------------------------

Start a stopped server without restarting the CLI:

.. code-block:: text

   > /mcp start github

   Starting "github"... done
   Tools registered: create_issue, list_repos, get_pr, ...

Stop a running server:

.. code-block:: text

   > /mcp stop filesystem

   Stopped "filesystem". Tools unregistered.

Tool registration and unregistration happen immediately — no agent restart is needed.

Restarting a Server
-------------------

Restart a server that has hung, crashed, or whose binary has been updated:

.. code-block:: text

   > /mcp restart my-db-server

   Stopping "my-db-server"... done
   Starting "my-db-server"... done
   Tools registered: query_db, list_tables, describe_table

Adding a Server at Runtime
---------------------------

``/mcp add`` registers a server without editing ``mcp.json`` by hand:

.. code-block:: text

   > /mcp add notes 'npx -y @modelcontextprotocol/server-memory'

   ✅ Added: notes
     Type: STDIO, Command: npx -y @modelcontextprotocol/server-memory
     Saved to .pantheon/mcp.json
     Use '/mcp start notes' to start

.. important::

   **This change is permanent.** ``/mcp add`` persists the new server to
   ``.pantheon/mcp.json``, so it is still configured in every future session. There is no
   session-only variant of the command — remove it with ``/mcp remove`` if you only
   wanted it once.

Adding a server does not start it. Either run ``/mcp start <name>`` afterwards, or pass
``--autostart`` to have it start automatically whenever the endpoint launches (which also
adds it to the ``auto_start`` list in ``mcp.json``).

The command takes the server's whole command line as one quoted argument, plus optional
flags:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Form
     - Description
   * - ``/mcp add <name> '<command>'``
     - Add a STDIO server that Pantheon launches as a subprocess.
   * - ``/mcp add <name> --uri <url>``
     - Add an HTTP server — an already-running remote endpoint, not managed by Pantheon.
   * - ``--autostart``
     - Start the server automatically on launch (adds it to ``auto_start``).
   * - ``--desc '<text>'``
     - A description, stored alongside the server entry.
   * - ``--env KEY=VALUE``
     - Set an environment variable for the subprocess. Repeatable.

.. code-block:: text

   > /mcp add ctx7 'uvx context7' --autostart --desc 'Context7 docs'
   > /mcp add bio 'uvx biomcp' --env API_KEY=xxx --env DEBUG=1
   > /mcp add remote --uri http://localhost:3000/mcp

Removing a Server
-----------------

.. code-block:: text

   > /mcp remove notes

   ✅ Removed: notes
     Removed from .pantheon/mcp.json

The server is stopped if it is running, its tools are unregistered, and its entry is
deleted from ``.pantheon/mcp.json`` — including from the ``auto_start`` list.

.. warning::

   ``/mcp remove`` **deletes the configuration**; it is not a session-only detach. If you
   only want to stop a server for now and keep it configured, use ``/mcp stop`` instead.

Tools from MCP
--------------

Once an MCP server is running, its tools are available to the agent without any
additional configuration. The agent discovers available tools automatically and can
use them in responses.

For example, after starting the ``filesystem`` server:

.. code-block:: text

   > List all CSV files in the /home/user/data directory

   I'll use the filesystem tool to list the files...
   [filesystem: list_directory /home/user/data *.csv]
   Found 12 CSV files: ...

MCP tools are shown in verbose mode (``/verbose`` or ``Ctrl+T``) with their server name
as a prefix so you can distinguish them from built-in toolset calls.

Troubleshooting
---------------

**Server fails to start**

Run the CLI with ``--log-level DEBUG`` to see the full MCP server stderr output:

.. code-block:: bash

   pantheon cli --log-level DEBUG

The debug output includes the server's startup command, environment, and any error
messages printed to stderr.

**Server shows Error status**

Check the error message with ``/mcp list`` — a brief error summary is shown for servers
in the Error state. Common causes:

- Missing executable (e.g., ``npx`` not on PATH, Python module not installed)
- Missing environment variable (e.g., API token not set)
- Port conflict if the server uses a fixed port

**Tools not appearing after start**

If ``/mcp start`` completes but no tools are listed, the server started but returned an
empty tool list. This usually means the server requires additional configuration (e.g.,
a missing API key in the ``env`` block) that prevents it from registering its tools.

.. tip::

   Most official MCP servers print useful startup messages to stderr. Use
   ``--log-level DEBUG`` to see them.
