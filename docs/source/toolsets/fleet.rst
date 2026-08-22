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
       fleet_id="my-fleet",                # required with nats_url
   )

   agent = Agent(
       name="cluster_manager",
       instructions="You manage a compute cluster."
   )
   await agent.toolset(fleet)
   await agent.chat()

Constructor arguments fall back to environment variables. There is no
``fleet.nats_url`` (or other fleet block) in ``settings.json``.

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Constructor
     - Environment variable
     - Role
   * - ``nats_url``
     - ``FLEET_NATS_URL``
     - Direct NATS URL (dev; bypass the Controller)
   * - ``fleet_id``
     - ``FLEET_ID``
     - Fleet id used with a direct NATS connection
   * - ``controller_url``
     - ``FLEET_CONTROLLER_URL``
     - Controller that maps an API key to a fleet (prod)
   * - ``key``
     - ``FLEET_KEY`` (or ``PANTHEON_API_KEY``)
     - API key for the Controller

Pass ``nats_url`` + ``fleet_id``, **or** ``controller_url`` + ``key``. The toolset
also honors ``FLEET_CREDS`` for an explicit NATS credentials file.

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
     - Run a shell command or Python snippet on a specific node.
       Signature: ``run_on_node(node_id, code, kind="shell", timeout=60)``.
       Streams stdout/stderr back. For ``kind="shell"``, write ``code`` in that
       node's native dialect (POSIX vs PowerShell).
   * - ``run_on_label``
     - Fan-out the same ``code`` to every node that carries ``label``.
       Signature: ``run_on_label(label, code, kind="shell", timeout=60)``.

Transfer
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Tool
     - Description
   * - ``transfer``
     - Node-to-node file move over the data plane.
       Signature: ``transfer(src_node, src_path, dst_node, dst_path, verify="sha256", compress="none", resume=False, wait=False, timeout=600)``.
       Supports SHA-256 integrity check, optional zstd compression, and resume from a partial transfer. Non-blocking by default; poll ``transfer_status``.
   * - ``broadcast``
     - Copy one file from ``src_node`` to many ``dst_nodes``.
       Signature: ``broadcast(src_node, src_path, dst_nodes, dst_path, ...)``.
   * - ``gather``
     - Pull the same path from many ``src_nodes`` onto one ``dst_node``.
       Signature: ``gather(src_nodes, src_path, dst_node, dst_dir, ...)``.
   * - ``transfer_status``
     - Check the progress of an in-flight or completed transfer by ``transfer_id``.

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
   Agent: [calls run_on_node(node_id="node-01", code="nvidia-smi")]
   → GPU 0: NVIDIA A100 80GB PCIe | Mem: 3241MiB / 81920MiB | ...

Run across a label group
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Restart the data pipeline on all GPU nodes.
   Agent: [calls run_on_label(label="gpu", code="systemctl restart pipeline")]
   → node-01: OK (exit 0)
   → node-04: OK (exit 0)

Transfer a script between nodes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Agent-side code — called by the fleet toolset
   await fleet.transfer(
       src_node="node-01",
       src_path="/home/user/scripts/preprocess.py",
       dst_node="node-04",
       dst_path="/opt/pipeline/preprocess.py",
       verify="sha256",
   )

Configuration
-------------

Fleet does **not** read ``.pantheon/settings.json``. Configure it on the
``FleetToolSet`` constructor or via environment variables (see Quick Start).

Example (direct NATS, development):

.. code-block:: bash

   export FLEET_NATS_URL="nats://your-hub:4222"
   export FLEET_ID="my-fleet"

Example (Controller, production):

.. code-block:: bash

   export FLEET_CONTROLLER_URL="https://controller.example.com"
   export FLEET_KEY="your-api-key"

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
