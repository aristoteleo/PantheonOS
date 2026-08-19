Pantheon Fleet (Web App)
========================

Pantheon Fleet allows you to connect and control multiple machines from a single UI session.
The web app includes a Fleet panel for node management, remote command execution, and token
lifecycle management.

Overview
--------

Fleet is a distributed control layer built on the NATS messaging hub. Each machine that
joins the fleet registers as a **node** and receives a unique identity. From the Fleet panel
in the UI you can:

- See all connected nodes and their status.
- Run shell commands or Python snippets on any node.
- Mint one-time join tokens for new machines.
- Revoke access for a node that is no longer trusted.

.. note::

   Fleet requires the NATS hub to be running (``--auto-start-nats`` or an external NATS
   cluster). All fleet traffic is routed through NATS — nodes do not need to be directly
   network-accessible from the UI host.

Starting a Local Fleet Node
---------------------------

To include the machine running the UI in the fleet, enable the fleet node alongside the
backend. There are two ways:

**Via settings**

In **Settings → Fleet**, toggle **Enable local fleet node** on. The node starts
automatically the next time you run ``pantheon ui``.

**Via the fleet toolset**

Include the ``FleetToolSet`` in your team template. When the backend starts, the fleet
node is registered automatically. Start the UI as usual (``pantheon ui`` has no
``--template`` flag):

.. code-block:: bash

   pantheon ui --auto-start-nats --auto-ui

The local node appears in the Fleet panel under the hostname of the current machine with
status **Connected**.

Joining the Fleet from Another Machine
----------------------------------------

Any machine that has ``pantheon-agents`` installed can join the fleet using a join token.

**Mint a join token**

In the Fleet panel, click **Mint Join Token**. A one-time token is generated and displayed.
The token expires after 24 hours or first use, whichever comes first. Copy the token or
use the QR code option to share it.

**Join from the remote machine**

On the remote machine, run:

.. code-block:: bash

   fleet join --token <token> --hub wss://your-nats-hub.example.com/nats

Replace ``<token>`` with the token from the UI and the ``--hub`` URL with your NATS
WebSocket endpoint. The remote machine connects to the hub, validates the token, and
registers as a fleet node.

After a few seconds the remote node appears in the Fleet panel with its hostname and
system information.

.. tip::

   If the UI and remote machine are both on the same local network using
   ``--auto-start-nats``, use ``ws://127.0.0.1:8080`` as the hub URL (accessible from
   within the local network when the NATS server is configured to bind to all interfaces).

Node List
---------

The Fleet panel displays a table of all connected nodes:

.. list-table::
   :header-rows: 1
   :widths: 20 20 20 20 20

   * - Column
     - Example
     - Description
     - -
     - -
   * - **Hostname**
     - ``gpu-server-01``
     - The machine's hostname
     - -
     - -
   * - **OS**
     - ``Linux x86_64``
     - Operating system and architecture
     - -
     - -
   * - **Label**
     - ``GPU Server``
     - Optional friendly label set at join time
     - -
     - -
   * - **Status**
     - ``Connected``
     - Connection state (Connected / Disconnected / Error)
     - -
     - -
   * - **Last Seen**
     - ``2 seconds ago``
     - Timestamp of the most recent heartbeat
     - -
     - -

Click a row to expand it and see available actions for that node.

Running Commands on Nodes
--------------------------

Select a node from the Fleet panel and use the command runner at the bottom of the node
detail view.

**Shell command**

Enter a shell command (e.g., ``nvidia-smi``, ``df -h``, ``ps aux``) and click **Run**.
Output streams back to the Fleet panel in real time. Commands time out after 60 seconds
by default.

**Python snippet**

Switch to the **Python** tab, enter a Python snippet, and click **Run**. The snippet runs
in a subprocess on the remote node using the system Python interpreter. ``stdout`` and
``stderr`` are streamed back.

.. note::

   Commands run as the OS user that started the fleet agent on the remote machine.
   Ensure that user has appropriate permissions for the operations you intend to perform.

Revoking a Node
---------------

To disconnect a node and prevent it from reconnecting:

1. Click the node's row in the Fleet panel.
2. Click **Revoke**.
3. Confirm the prompt.

The node's token is invalidated on the hub. The node receives a disconnection message and
cannot rejoin with the same token. A new token must be minted to re-add the machine.

Fleet Down
----------

To stop the local fleet node (the node running on the same machine as the UI):

Go to **Settings → Fleet** and click **Stop Local Node**, or toggle **Enable local fleet
node** off.

For remote nodes, stopping fleet requires running ``fleet down`` on the remote machine:

.. code-block:: bash

   fleet down

This gracefully disconnects the node from the NATS hub.

Further Reading
---------------

- For the full fleet toolset API (available to agents): :doc:`/toolsets/fleet`
- For the fleet CLI and Go binary documentation: see ``docs/pantheon-fleet.md`` in the
  repository.
