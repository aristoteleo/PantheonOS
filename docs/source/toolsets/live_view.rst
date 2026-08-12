Live View Toolset
=================

The ``LiveViewToolSet`` lets agents open interactive, browser-rendered visualizations alongside the conversation. When an agent calls ``open_live_view``, a panel appears in the web or desktop app showing a live, data-driven visualization — no separate tool, no copy-pasting outputs.

Overview
--------

Live View covers a wide range of scientific and data visualization types:

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Viewer
     - Type
     - Best For
   * - **IGV**
     - Genomics
     - Genome browser: alignments, variants, tracks (BAM, VCF, BED, …)
   * - **Vitessce**
     - Single-cell / spatial
     - Multi-modal single-cell and spatial omics dashboards
   * - **Viv**
     - Microscopy
     - High-resolution OME-TIFF and Zarr microscopy images
   * - **Mol***
     - Structural biology
     - Interactive 3D molecular structures (PDB, mmCIF, SDF)
   * - **Gosling**
     - Genomics charts
     - Circular and linear genomic visualizations
   * - **Cytoscape**
     - Networks
     - Biological and general network / graph visualization
   * - **MSA**
     - Bioinformatics
     - Multiple sequence alignment viewer
   * - **Phylotree**
     - Bioinformatics
     - Phylogenetic tree visualization
   * - **RDKit**
     - Cheminformatics
     - 2D and 3D chemical structure rendering
   * - **Spatial3D**
     - Spatial omics
     - 3D spatial transcriptomics point clouds
   * - **Volume3D**
     - Imaging
     - 3D volumetric data rendering

Quick Start
-----------

.. code-block:: python

   from pantheon.agent import Agent
   from pantheon.toolsets.live_view import LiveViewToolSet

   live = LiveViewToolSet(name="live_view")

   agent = Agent(
       name="analyst",
       instructions="You are a bioinformatics analyst."
   )
   await agent.toolset(live)
   await agent.chat()

The Live View panel appears automatically in the web/desktop app when the agent opens a view.

Tool Reference
--------------

``open_live_view``
~~~~~~~~~~~~~~~~~~

Opens an interactive visualization panel. Parameters vary by viewer type.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Parameter
     - Description
   * - ``viewer``
     - Viewer type: ``"igv"``, ``"vitessce"``, ``"mol*"``, ``"viv"``, ``"gosling"``, ``"cytoscape"``, ``"msa"``, ``"phylotree"``, ``"rdkit"``, ``"spatial3d"``, ``"volume3d"``
   * - ``data``
     - Data payload or URL — format depends on viewer (see examples below)
   * - ``title``
     - Optional title for the panel
   * - ``config``
     - Optional viewer-specific configuration dict

``list_live_views``
~~~~~~~~~~~~~~~~~~~

Returns all currently open Live View panels with their IDs, titles, and viewer types.

``close_live_view``
~~~~~~~~~~~~~~~~~~~

Closes a specific Live View panel by ID.

``update_live_view``
~~~~~~~~~~~~~~~~~~~~

Push updated data to an open Live View panel without reopening it.

Usage Examples
--------------

Genome browser (IGV)
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   await live.open_live_view(
       viewer="igv",
       title="Sample alignments",
       data={
           "genome": "hg38",
           "tracks": [
               {"type": "alignment", "url": "s3://bucket/sample.bam"},
               {"type": "variant",   "url": "s3://bucket/sample.vcf.gz"},
           ]
       }
   )

3D molecular structure (Mol*)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   await live.open_live_view(
       viewer="mol*",
       title="Protein structure",
       data={"pdb_id": "1CRN"}          # fetches from RCSB PDB
       # or: data={"url": "https://files.rcsb.org/download/1CRN.pdb"}
   )

Network graph (Cytoscape)
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import networkx as nx

   G = nx.karate_club_graph()
   elements = nx.cytoscape_data(G)["elements"]

   await live.open_live_view(
       viewer="cytoscape",
       title="Karate club graph",
       data={"elements": elements}
   )

In a Conversation
~~~~~~~~~~~~~~~~~

.. code-block:: text

   User: Visualize the structure of the EGFR kinase domain.
   Agent: [calls open_live_view(viewer="mol*", data={"pdb_id": "1IEP"})]
   → Live View panel opens in the UI showing 3D structure.

   User: Now show me mutations from the VCF file.
   Agent: [calls open_live_view(viewer="igv", data={...})]
   → Second panel opens showing the genome browser.

How It Works
------------

Live View is served by the Pantheon endpoint. When ``open_live_view`` is called:

1. The agent writes the data payload to a temporary serve path on the endpoint.
2. The endpoint notifies the frontend via NATS.
3. The frontend opens a panel and loads the viewer component with the data URL.
4. Data updates (``update_live_view``) push diffs without a full reload.

.. note::

   Live View panels are only visible in the web and desktop app. They do not appear in the CLI or Python API — those interfaces receive a text confirmation that the view was opened.

See Also
--------

- :doc:`/interfaces/ui/live-view` — Using Live View from the web/desktop app
- Live View skill library (in ``.pantheon/skills/live_view/``) — ready-made agent prompts for each viewer
