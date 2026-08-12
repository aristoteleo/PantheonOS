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

Add a new MCP server for the current session without editing ``mcp.json``:

.. code-block:: text

   > /mcp add notes npx @modelcontextprotocol/server-memory

   Starting "notes"... done
   Tools registered: store_memory, retrieve_memory, list_memories

The server is active for this session only. To make it permanent, add it to
``.pantheon/mcp.json`` manually.

For servers requiring arguments:

.. code-block:: text

   > /mcp add myfs npx @modelcontextprotocol/server-filesystem /tmp/workspace

Removing a Server
-----------------

Remove an MCP server from the current session:

.. code-block:: text

   > /mcp remove notes

   Stopped "notes". Tools unregistered and server removed from session.

This does not modify ``mcp.json`` — the server remains configured for future sessions.

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
