Live View
=========

Live View lets agents open interactive, in-browser visualizations alongside the chat —
genomics browsers, 3D structure viewers, spatial maps, network graphs, and more. No external
tool or separate application is required.

Overview
--------

When an agent calls the Live View API, a visualization panel opens in the UI next to the
chat. The panel renders domain-specific viewers that update in real time as the agent
provides data. Users can interact with the viewers (zoom, pan, select, query) while the
conversation continues.

Live View is designed for scientific and analytical workflows where visual inspection of
data is an integral part of the analysis — not an afterthought that requires switching
to a separate application.

How It Works
------------

1. The agent receives a task that requires visualization (e.g., "show the coverage at this
   locus").
2. The agent calls ``open_live_view`` via the ``LiveViewToolSet``, specifying the viewer
   type and data.
3. The backend serializes the data and publishes it to the NATS hub.
4. The UI receives the message and renders the appropriate viewer component in the Live
   View side panel.
5. Data updates stream in real time as the agent runs — for example, as sequences are
   aligned or as a simulation progresses.

The viewer remains open until the user closes it or the agent closes it programmatically.
Multiple views can be open simultaneously in separate tabs within the Live View panel.

Available Viewers
-----------------

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - Viewer
     - Type tag
     - Description
   * - **IGV**
     - ``igv``
     - Integrative Genomics Viewer — genome browser with track support for BAM, VCF,
       BED, BigWig, and GFF formats.
   * - **Vitessce**
     - ``vitessce``
     - Single-cell spatial data explorer supporting AnnData/Zarr datasets, UMAP
       embeddings, spatial coordinates, and image layers.
   * - **Viv**
     - ``viv``
     - High-resolution microscopy image viewer supporting OME-TIFF and OME-Zarr
       formats with multi-channel rendering and z-stack navigation.
   * - **Mol***
     - ``molstar``
     - 3D molecular structure viewer supporting PDB, mmCIF, SDF, and MOL2 formats.
       Full rotation, selection, and measurement tools.
   * - **Gosling**
     - ``gosling``
     - Scalable genomics visualization grammar for tracks, circular layouts, and
       linked multi-track displays.
   * - **Cytoscape**
     - ``cytoscape``
     - Network and graph viewer for biological networks, interaction graphs, and
       pathway maps.
   * - **MSA**
     - ``msa``
     - Multiple sequence alignment viewer with color schemes for nucleotide and amino
       acid conservation.
   * - **Phylotree**
     - ``phylotree``
     - Phylogenetic tree renderer supporting Newick and Nexus formats with node
       annotation and clade highlighting.
   * - **RDKit**
     - ``rdkit``
     - Chemical structure viewer for small molecules using RDKit SMILES and SDF
       rendering.
   * - **Spatial3D**
     - ``spatial3d``
     - 3D spatial transcriptomics viewer for point clouds with cell-type color
       mapping.
   * - **Volume3D**
     - ``volume3d``
     - Volumetric rendering for 3D microscopy datasets and medical imaging volumes
       (NIfTI, HDF5).

Opening a Live View Manually
-----------------------------

The Live View panel is available in the UI even without an agent actively pushing data.

1. Click the **Live View** icon in the sidebar (the waveform icon).
2. The panel opens showing any active views.
3. Use **+ New View** to open a viewer manually by selecting the type and optionally
   providing a data file from the workspace.
4. To close a view, click the **×** on its tab.
5. To refresh a view (e.g., if data on disk has changed), click the **↻** button on the
   tab.

Live View in Agent Workflows
-----------------------------

Agents use the ``LiveViewToolSet`` to open and update views programmatically.

.. code-block:: python

   from pantheon.toolsets.live_view import LiveViewToolSet

   toolset = LiveViewToolSet()

   # Open an IGV genome browser at a specific locus
   await toolset.open_live_view(
       view_type="igv",
       view_id="coverage_track",
       data={
           "genome": "hg38",
           "locus": "chr7:117,120,016-117,308,718",
           "tracks": [
               {"type": "bam", "url": "s3://my-bucket/sample.bam"},
               {"type": "vcf", "url": "s3://my-bucket/variants.vcf.gz"},
           ],
       },
   )

   # Update the view with new data (the panel refreshes in place)
   await toolset.update_live_view(
       view_id="coverage_track",
       data={"locus": "chr7:117,500,000-117,600,000"},
   )

   # Close a view when analysis is complete
   await toolset.close_live_view(view_id="coverage_track")

.. note::

   Live View data is transferred over NATS. Very large data files (>100 MB) should be
   served from a URL (HTTP, S3, GCS) rather than inlined in the tool call payload.
   Viewer components that support URL-based data (IGV, Viv, Vitessce) fetch data
   directly from the source.

See also: :doc:`/toolsets/live_view` for the complete ``LiveViewToolSet`` API reference.
