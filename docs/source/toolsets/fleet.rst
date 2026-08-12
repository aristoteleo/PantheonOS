Pantheon Fleet Toolset
======================

The ``FleetToolSet`` gives agents the ability to observe and control a cluster of machines registered on the same Pantheon Fleet hub. Agents can list nodes, run code or shell commands on remote nodes, transfer files, and receive results — all without leaving a conversation.

Overview
--------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Capability
     - Description
   * - **Observe**
     - List nodes, query node info and labels, check fleet status
   * - **Execute**
     - Run shell commands or Python snippets on any node or label group
   * - **Transfer**
     - Send or gather files across nodes with resume, compression, and integrity checks
   * - **Broadcast**
     - Fan-out a command to all matching nodes simultaneously

Quick Start
-----------

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets.fleet import FleetToolSet

   fleet = FleetToolSet(
       name="fleet",
       nats_url="nats://localhost:4222",   # hub NATS address
   )

   agent = Agent(
       name="cluster_manager",
       instructions="You manage a compute cluster."
   )
   await agent.toolset(fleet)
   await agent.chat()

The ``nats_url`` can also be set in ``.pantheon/settings.json`` under ``fleet.nats_url``.

Tool Reference
--------------

Observe
~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Tool
     - Description
   * - ``fleet_list_nodes``
     - Return all connected nodes with hostname, OS, labels, and last-seen time.
   * - ``fleet_node_info``
     - Detailed information about a specific node: capabilities, labels, resource tags.
   * - ``fleet_status``
     - High-level cluster health: number of nodes, unreachable nodes, hub latency.
   * - ``fleet_pick_node``
     - Select the best node matching given criteria (OS, label, resource availability).

Execute
~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Tool
     - Description
   * - ``run_on_node``
     - Run a shell command or Python snippet on a specific node. Streams stdout/stderr back. OS-aware (Windows vs Unix path handling).
   * - ``run_on_label``
     - Fan-out a command to all nodes that match a label. Returns per-node results.

Transfer
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Tool
     - Description
   * - ``transfer``
     - Send a file to a remote node. Supports SHA-256 integrity check, zstd compression, and resume from partial transfer.
   * - ``broadcast``
     - Send a file to all matching nodes simultaneously.
   * - ``gather``
     - Retrieve a file from a remote node back to the hub machine.
   * - ``transfer_status``
     - Check the progress of an in-flight or completed transfer.

Usage Examples
--------------

List all nodes
~~~~~~~~~~~~~~

.. code-block:: text

   User: Show me all nodes in the fleet.
   Agent: [calls fleet_list_nodes]
   → node-01  Linux  labels: [gpu, research]  last seen: 2s ago
   → node-02  macOS  labels: [dev]            last seen: 5s ago
   → win-03   Windows labels: [build]         last seen: 12s ago

Run a command on one node
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Run `nvidia-smi` on node-01 to check GPU status.
   Agent: [calls run_on_node(node="node-01", command="nvidia-smi")]
   → GPU 0: NVIDIA A100 80GB PCIe | Mem: 3241MiB / 81920MiB | ...

Run across a label group
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Restart the data pipeline on all GPU nodes.
   Agent: [calls run_on_label(label="gpu", command="systemctl restart pipeline")]
   → node-01: OK (exit 0)
   → node-04: OK (exit 0)

Transfer a script to all nodes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Agent-side code — called by the fleet toolset
   await fleet.transfer(
       source="/home/user/scripts/preprocess.py",
       destination="/opt/pipeline/preprocess.py",
       label="gpu",
       verify=True,
   )

Configuration
-------------

Fleet connection settings live in ``.pantheon/settings.json``:

.. code-block:: json

   {
     "fleet": {
       "nats_url": "nats://your-hub:4222",
       "controller_api_key": "optional-key-for-http-api"
     }
   }

``controller_api_key`` is only needed if your fleet hub exposes an HTTP management API.

Joining a Fleet
---------------

To add a machine to the fleet, install the fleet binary and run:

.. code-block:: bash

   # On the remote machine
   fleet join --token <token-from-hub>

Tokens are minted from the Web App (see :doc:`/interfaces/ui/fleet`) or programmatically.

See Also
--------

- :doc:`/interfaces/ui/fleet` — Fleet management from the web/desktop app
- :doc:`/advanced/distributed` — Distributed deployment architecture
- ``docs/pantheon-fleet.md`` in the repository — Fleet binary installation and Go CLI reference
