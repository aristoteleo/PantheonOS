Desktop & App Windows
=====================

The web and desktop app present an **Atrium desktop**: a workspace of application windows
that the agent and the user share. Genomics browsers, 3D structure viewers, spatial maps,
microscopy images, network graphs and the web browser all open as windows there, and both
of you can drive them.

.. note::

   **This replaces the old Live View panel.** Earlier releases showed agent-driven
   visualizations as per-view sessions in the chat sidebar, opened with ``live_view_*``
   tools. That plane is retired. A window now belongs to the machine and appears in every
   viewport of it, instead of living in one browser tab — so a second browser window, or
   the desktop app alongside the web app, shows the same desktop.

How It Works
------------

1. The agent receives a task that calls for a viewer — "show the coverage at this locus".
2. It calls ``desktop_open`` from the :doc:`/toolsets/desktop`, either naming an app or
   letting the file extension route to one, exactly as a double-click would.
3. The pod records the new window in the desktop document and publishes the change over
   NATS.
4. Every attached viewport opens the window and loads the app.
5. The agent steers the window in place with ``desktop_read`` / ``desktop_update`` /
   ``desktop_set`` / ``desktop_call``, and verifies with ``desktop_screenshot``.

Windows are long-lived. They stay open until you close them or the agent closes one
deliberately, and re-opening a file that is already open just focuses the window that has
it rather than piling up duplicates.

Available Apps
--------------

.. list-table::
   :header-rows: 1
   :widths: 20 22 58

   * - App
     - ``app_id``
     - Description
   * - **IGV**
     - ``igv``
     - Integrative Genomics Viewer — genome browser with track support for alignments,
       variants, and coverage.
   * - **Gosling**
     - ``gosling``
     - Scalable genomics visualization grammar: tracks, circular layouts, and linked
       multi-track displays.
   * - **Vitessce**
     - ``vitessce``
     - Single-cell and spatial explorer for ``.h5ad`` datasets — embeddings, spatial
       coordinates, and image layers.
   * - **Spatial 3D**
     - ``spatial3d``
     - 3D spatial transcriptomics point clouds from ``.h5ad``, with cell-type colour
       mapping.
   * - **Viv**
     - ``viv``
     - High-resolution microscopy viewer for OME-TIFF and OME-Zarr, with multi-channel
       rendering.
   * - **Volume 3D**
     - ``volume3d``
     - Volumetric rendering of 3D imaging datasets from Zarr.
   * - **Mol***
     - ``molstar``
     - 3D molecular structures from PDB, CIF, and mmCIF, with rotation, selection, and
       measurement.
   * - **RDKit**
     - ``rdkit``
     - Chemical structures from SDF, MOL, and SMILES.
   * - **Cytoscape**
     - ``cytoscape``
     - Network and graph viewer for interaction graphs and pathway maps (``.cyjs``).
   * - **MSA**
     - ``msa``
     - Multiple sequence alignment viewer for FASTA and alignment files.
   * - **PhyloTree**
     - ``phylotree``
     - Phylogenetic trees from Newick files, with node annotation and clade
       highlighting.
   * - **Browser**
     - ``browser``
     - A real, shared Chromium page. You can click, type, and sign in on it while the
       agent drives the same page.

Apps are installed packages, so this list reflects what is on the machine rather than a
fixed set. The agent reads the live list with ``desktop_apps()``; each app documents its
own state contract at ``.pantheon/apps/<app_id>/skill/SKILL.md``.

Working With Windows
--------------------

- **Open** — double-click a file in the file browser, or pick an app from the launcher.
  The same routing the agent uses applies: the extension decides the app unless you name
  one.
- **Spaces** — the desktop has mac-style virtual spaces, numbered from 1. Each window
  lives on one; focusing or opening a window on another space carries you there.
- **Move, resize, minimize, close** — as on any desktop. Windows you opened yourself are
  first-class: the agent can find them with ``desktop_windows()`` and read, update, or
  call them exactly as if it had opened them.
- **Share** — because the desktop belongs to the machine rather than to a tab, every
  viewport you have attached shows the same windows.

Asking the Agent for a Window
-----------------------------

You do not need to name tools. Ask in the chat:

.. code-block:: text

   User: Open the alignment for sample 3 and jump to EGFR.
   Agent: [desktop_open routes the file to igv, then desktop_update sets the locus]

   User: That colour map is unreadable — use viridis.
   Agent: [desktop_update on the same window_id]

The second request corrects the open window rather than opening a new one, which is the
behaviour the toolset is built around.

Serving Local Data
------------------

Apps that fetch their data by URL need it reachable over HTTP. The agent handles this
with ``serve_local_data``, which exposes a workspace path through a local CORS static
server. Files must live under the workspace for this to work. For simply *showing* a
file, ``desktop_open`` is the right tool — it runs the app's whole open pipeline,
including any format conversion, without a separate serve step.

Building an App
---------------

When no installed app fits, the agent can build one:

- **A bespoke window** — a single frontend module passed to ``desktop_open(module=...)``,
  with no manifest and no install. Good for a one-off UI.
- **An installed package** — a directory under ``.pantheon/apps/<id>/`` with a manifest,
  a frontend entry point, and optionally a Python backend and a skill. Writing the files
  *is* the install; it is discovered on the next open, claims file types, and can be
  reused.

Both are drivable with the same ``desktop_read`` / ``desktop_update`` / ``desktop_set`` /
``desktop_call`` tools as a packaged app.

See Also
--------

- :doc:`/toolsets/desktop` — the complete ``DesktopToolSet`` tool reference
- :doc:`terminal` — the integrated PTY terminal
- :doc:`core-features` — chat, sessions, and context
