Desktop Toolset
===============

The ``DesktopToolSet`` is the agent's hands and eyes on the user's desktop. It lists the
apps installed on the machine, opens files in them as **windows**, reads and steers the
state of any window — including windows the *user* opened — drives a shared Chromium
browser, and serves workspace files over HTTP to the apps that need them.

.. code-block:: python

   from pantheon.toolsets.desktop import DesktopToolSet

The class is also re-exported from ``pantheon.toolsets`` as ``DesktopToolSet`` and, for
saved agent configurations written before the rename, as ``LiveViewToolSet``.

.. note::

   **The old Live View plane is retired.** Earlier releases exposed per-view sessions in
   the chat sidebar through ``live_view_*`` tools (``open_live_view``,
   ``update_live_view``, ``close_live_view``) and ``report_view_state``. Those tools no
   longer exist. Atrium windows replaced them: a window belongs to the machine and is
   visible in *every* viewport, rather than to a single browser tab. A desktop-native
   live view is planned and will be built on the window document, not on the old plane.

Overview
--------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Capability
     - Description
   * - **Discover**
     - ``desktop_apps`` for what is installed, ``desktop_windows`` for what is open
   * - **Open**
     - ``desktop_open`` — route a file by extension, name an app, or supply a bespoke
       frontend module
   * - **Steer**
     - ``desktop_read`` / ``desktop_update`` / ``desktop_set`` / ``desktop_call`` on any
       controllable window
   * - **Verify**
     - ``desktop_screenshot`` returns what the window actually shows
   * - **Browse**
     - ``browser_open`` and the ``browser_*`` tools drive one shared, real Chromium page
   * - **Serve**
     - ``serve_local_data``, ``serve_endpoint``, and ``manage_endpoints`` hand workspace
       files and custom HTTP handlers to apps

Quick Start
-----------

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets.desktop import DesktopToolSet

   desktop = DesktopToolSet(name="desktop")

   agent = Agent(
       name="analyst",
       instructions="You are a bioinformatics analyst.",
   )
   await agent.toolset(desktop)
   await agent.chat()

Windows open on the user's Atrium desktop in the web or desktop app. The CLI and the
Python API can call the tools, but there has to be a desktop attached to the chat for a
window to appear; tools that need one return ``{"success": False, "error": ...}``
otherwise.

Installed Apps
--------------

``desktop_apps()`` is the authoritative list — never guess an ``app_id``. The apps
shipped with PantheonOS cover the common scientific viewers:

.. list-table::
   :header-rows: 1
   :widths: 18 22 60

   * - ``app_id``
     - Domain
     - Opens
   * - ``igv``
     - Genomics
     - Genome browser: alignments, variants, and tracks
   * - ``gosling``
     - Genomics
     - Circular and linear genomic visualizations
   * - ``vitessce``
     - Single-cell / spatial
     - ``.h5ad`` — multi-modal single-cell and spatial dashboards
   * - ``spatial3d``
     - Spatial omics
     - ``.h5ad`` — 3D spatial transcriptomics point clouds
   * - ``viv``
     - Microscopy
     - ``.ome.tif``, ``.ome.tiff``, ``.ome.zarr``, ``.zarr``, ``.tif``, ``.tiff``
   * - ``volume3d``
     - Imaging
     - ``.zarr`` — 3D volumetric rendering
   * - ``molstar``
     - Structural biology
     - ``.pdb``, ``.cif``, ``.mmcif`` — interactive 3D structures
   * - ``rdkit``
     - Cheminformatics
     - ``.sdf``, ``.mol``, ``.smi`` — 2D/3D chemical structures
   * - ``cytoscape``
     - Networks
     - ``.cyjs`` — biological and general graph visualization
   * - ``msa``
     - Bioinformatics
     - ``.fasta``, ``.fa``, ``.aln`` — multiple sequence alignments
   * - ``phylotree``
     - Bioinformatics
     - ``.nwk``, ``.newick``, ``.tree`` — phylogenetic trees
   * - ``browser``
     - Web
     - The shared Chromium page opened by ``browser_open``

Each installed app ships its own state contract at
``.pantheon/apps/<app_id>/skill/SKILL.md``. Read it before driving an app with
non-trivial state.

Tool Reference
--------------

Discover
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Tool
     - Description
   * - ``desktop_apps()``
     - What is installed: ``app_id``, ``name``, ``description``, ``opens`` (claimed file
       extensions), ``actions``, ``backend``, and ``skill`` (path of its state contract).
   * - ``desktop_windows()``
     - Every open window — whoever opened it: ``window_id``, ``app_id``, ``title``,
       ``path``, ``space``, ``minimized``, ``status``, and ``opened_by``, plus
       ``space_count`` and ``active_space`` for the desktop's virtual spaces.

Open
~~~~

``desktop_open(app="", path="", state={}, window_id="", module="", title="")``

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Parameter
     - Description
   * - ``app``
     - App id from ``desktop_apps()``. Omit it together with ``path`` to route by file
       type, exactly as a double-click does.
   * - ``path``
     - File to open. The app's own open pipeline runs (backend prepare, format
       conversion) — you do **not** need ``serve_local_data`` first.
   * - ``state``
     - Initial state, instead of or merged over a file, for apps whose state *is* a
       config (Vitessce, Gosling, IGV).
   * - ``window_id``
     - Show this in an **existing** window rather than opening a new one.
   * - ``module``
     - A frontend ES-module source you wrote — ``export function setup(app, root) { … }``
       — opened as a bespoke window with no manifest and no install.
   * - ``title``
     - Window title, used with ``module``.

Returns ``window_id``, and ``reused: true`` when the file was already open in a window.

Steer
~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Tool
     - Description
   * - ``desktop_read(window_id)``
     - The window's current state, in the shape its skill documents.
   * - ``desktop_update(window_id, patch)``
     - Deep-merge a patch into the state. Adds and changes, never removes.
   * - ``desktop_set(window_id, state)``
     - Replace the state wholesale — for when the config is wrong rather than
       incomplete.
   * - ``desktop_call(window_id, action, args={})``
     - Invoke a named action, the same handler the app's own menu triggers.
       ``action="$close"`` closes the window.
   * - ``desktop_screenshot(window_id)``
     - What the window actually shows, returned inline for vision-capable models and
       saved to disk.

Windows are long-lived and they belong to the user. When a view is wrong, correct it in
place rather than opening another window.

Browse
~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Tool
     - Description
   * - ``browser_open(url="", show=True)``
     - Start a Chromium page and, by default, show it as a Browser window. Returns
       ``page_id``.
   * - ``browser_goto(url, page_id="")``
     - Navigate a page; the user watching the window sees it happen.
   * - ``browser_read(page_id="")``
     - The page's readable text content.
   * - ``browser_click(selector, page_id="")``
     - Click an element by CSS selector.
   * - ``browser_type(selector, text, ...)``
     - Type into an element.
   * - ``browser_screenshot(page_id="", path="")``
     - Capture the page as an image.
   * - ``browser_pages()`` / ``browser_close(page_id)``
     - List open pages; close one.

The page is *shared*: the user can click, type, and sign in on it while the agent drives
the same page. The browser profile persists in the sandbox, so a site the user logged
into stays logged in.

Serve
~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Tool
     - Description
   * - ``serve_local_data(path)``
     - Expose a workspace file or directory over HTTP with CORS, for apps that fetch
       their data by URL. Returns ``base_url`` and ``url``. To *show* a file, prefer
       ``desktop_open`` — it runs the app's whole open pipeline.
   * - ``serve_endpoint(...)``
     - Register a Python HTTP handler from the workspace on the data server.
   * - ``manage_endpoints(...)``
     - List, inspect, and remove registered endpoints.

Usage Examples
--------------

Open a file the way a double-click would
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   opened = await desktop.desktop_open(path="/workspace/scan.ome.tif")
   window_id = opened["result"]["window_id"]     # routed to viv by extension

Name the app explicitly
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # .h5ad is claimed by both vitessce and spatial3d — say which one you want
   await desktop.desktop_open(app="spatial3d", path="data/cells.h5ad")

3D molecular structure
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   await desktop.desktop_open(app="molstar", path="structures/1crn.pdb")

Fix the window you have
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   state = (await desktop.desktop_read(window_id))["result"]["state"]
   channel = {**state["channels"][0], "color": [255, 0, 0]}
   await desktop.desktop_update(window_id, {"channels": [channel]})

   # then verify with your eyes, not just the state
   await desktop.desktop_screenshot(window_id)

Steer a window the user opened
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   windows = (await desktop.desktop_windows())["result"]["windows"]
   tree = next(w for w in windows if w["app_id"] == "phylotree")
   await desktop.desktop_update(tree["window_id"], {"layout": "radial"})

A bespoke window, with no install
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   await desktop.desktop_open(
       title="Counter",
       state={"n": 5},
       module="""
   export function setup(app, root) {
     app.onState((s) => {
       root.innerHTML = `<div style="padding:24px">count: ${s.n ?? 0}</div>`
     })
     app.defineAction("bump", () => {
       const n = (app.state?.n ?? 0) + 1
       app.setState({ n })
       return n
     })
     app.ready()
   }
   """,
   )

In a Conversation
~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Show me the structure of the EGFR kinase domain.
   Agent: [calls desktop_open(app="molstar", path="1iep.pdb")]
   → A Mol* window opens on the desktop, visible in every viewport.

   User: Colour the activation loop.
   Agent: [calls desktop_read, then desktop_update on the same window_id]
   → The open window updates in place; no second window appears.

How It Works
------------

Which windows exist is a **document owned by the pod**, not by a browser tab
(``pantheon/toolsets/desktop/desktop_session.py``). Clients — the agent and every open
viewport — send *intents*, the pod applies them in one place, and the resulting delta is
published over NATS to every viewport. Applying deltas by sequence number makes that
idempotent, so a viewport can show its own change immediately and safely drop the echo
of its own broadcast when it arrives.

That is why a window reaches every viewport rather than one tab, and why
``desktop_windows()`` answers correctly even with no desktop currently on screen: the
window list is a property of the machine.

See Also
--------

- :doc:`/interfaces/ui/desktop` — using the desktop from the web/desktop app
- ``.pantheon/skills/desktop/SKILL.md`` — the agent-facing skill for this toolset
- ``.pantheon/apps/<app_id>/skill/SKILL.md`` — each app's state contract
