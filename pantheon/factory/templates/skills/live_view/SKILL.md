---
id: live_view_index
name: LiveView Skills Index
description: |
  Skills for opening and driving agent-controllable visualization
  components in the Pantheon UI sidebar — interactive viewers the agent
  can open, control, and read back. Viewers: Vitessce (spatial / single-
  cell omics), Viv (bioimage / microscopy), plus agent-generated apps.
tags: [live-view, visualization, vitessce, viv, bioimage, spatial, single-cell, interactive]
---

# LiveView — Agent-Controllable UI Components

A **LiveView** is an interactive component the agent opens in the Pantheon
UI's right sidebar, then drives and observes through the `live_view` tools.
Unlike a static plot image, a LiveView is live: the agent changes its state
and the user sees it update; the user interacts with it and the agent reads
the result back.

Load the relevant skill file before building a visualization.

**Architecture** — only the LiveView SDK runtime is built in. Every viewer
is a **plugin**: a folder `skills/live_view/<name>/` holding `<name>.md`
(this guide), `adapter.js` (a `setup(lv, root)` module), and an optional
`demo.json`. `open_live_view` either resolves a named viewer plugin
(`view_type="vitessce"`) or loads an agent-generated component
(`view_type="custom"` + `module_url`). Adding a viewer = dropping a new
`<name>/` folder here; no app code changes.

## Available skills

### Vitessce — spatial / single-cell / imaging data

Open a Vitessce browser to explore spatial transcriptomics, single-cell, and
microscopy-imaging datasets: spatial scatterplots, gene-expression coloring,
heatmaps, cell-set selection, image layers.

**Skill file**: [vitessce/vitessce.md](./vitessce/vitessce.md)

**When to use**:
- Visualizing spatial transcriptomics (10x Visium, Xenium, MERFISH, …)
- Single-cell data with embeddings (UMAP/t-SNE) the user should explore
- Cell segmentations as **interactive** objects — click/hover a cell,
  colour by type or gene. (Just *viewing* boundaries on an image → use Viv;
  Vitessce does not render clean boundaries.)
- Spatial omics where cells/sets/embeddings matter, not just the image

### Viv — bioimage / microscopy viewer

Open a Viv viewer for high-resolution, multiplexed bioimaging — OME-TIFF
and OME-Zarr (OME-NGFF): multichannel fluorescence, microscopy, IF/IMC/
CODEX, whole-slide images. Channel colors, contrast, pan/zoom, overview.

**Skill file**: [viv/viv.md](./viv/viv.md)

**When to use**:
- The data *is an image* — OME-TIFF / OME-Zarr microscopy
- Multichannel fluorescence the user wants to recolour / adjust
- Cloud-hosted or local bioimages (served via `serve_local_data`)
- **Overlaying a cell segmentation / showing cell boundaries** on an image
  (boundaries as an extra channel — the preferred way to *view* a mask)

### Mol* — 3D molecular structures

Open a Mol* viewer for 3D macromolecular structures — proteins, nucleic
acids, complexes — from the RCSB PDB, the AlphaFold DB, or a local
`.pdb` / `.cif` file. Rotate, zoom, inspect; AlphaFold models colour by
pLDDT.

**Skill file**: [molstar/molstar.md](./molstar/molstar.md)

**When to use**:
- Showing a protein / nucleic-acid 3D structure (experimental or predicted)
- Visualising an AlphaFold prediction
- Any `.pdb` / `.cif` structure file (see also the `structural_biology` skill
  for obtaining / predicting structures)

### IGV — genome browser (tracks on a reference)

Open an IGV.js genome browser to view BAM/CRAM alignments, VCF variants,
BED/GFF annotations, bigWig coverage — on a reference genome (hg38, mm10,
custom FASTA, …). Pan, zoom, jump to a gene or locus.

**Skill file**: [igv/igv.md](./igv/igv.md)

**When to use**:
- "Look at this region in a genome browser" — RNA-seq pileups, ChIP/ATAC
  peaks, variant calling, splice junctions, CRISPR-screen hits
- Any BAM/CRAM/VCF/BED/GFF/bigWig on a reference genome
- A gene symbol or coordinate range to display

### Gosling — designed genomic figures (Vega-Lite-like grammar)

Open a Gosling.js view for **designed** genomic visualisations — circular
ideograms, multi-track / multi-sample layouts, comparative dual-genome
views, custom encodings. Driven by a declarative JSON **spec**.

**Skill file**: [gosling/gosling.md](./gosling/gosling.md)

**When to use**:
- Circular chromosome ideograms / circos-style plots
- Designed multi-track or sample-faceted genomic figures
- Comparative dual-genome / synteny visualisations
- Anything that's hard to build with matplotlib but easy with a grammar
- Use IGV instead if the task is to *look at a BAM / VCF / BED file at a
  locus*; the two viewers do not overlap.

### Cytoscape — biological networks & pathways

Open a Cytoscape.js view for interactive networks — protein-protein
interactions, signalling / metabolic / regulatory pathways, ontology
graphs. Nodes + edges as JSON, built-in layouts (cose, breadthfirst,
circle, dagre, ...), CSS-like stylesheet by selectors.

**Skill file**: [cytoscape/cytoscape.md](./cytoscape/cytoscape.md)

**When to use**:
- PPI networks (STRING, BioGRID, IntAct)
- Signalling / metabolic / regulatory pathways
- Gene-regulatory networks (TF → target)
- Any graph the user benefits from *interactively* (drag, hover, zoom)

### MSA — multiple sequence alignment viewer

Open a multiple-sequence-alignment view (EBI Nightingale's
`<nightingale-msa>`) for protein or DNA alignments. Standard colour
schemes (clustal, taylor, hydro, zappo, ...), configurable tile sizes.

**Skill file**: [msa/msa.md](./msa/msa.md)

**When to use**:
- Display a pre-computed alignment from MAFFT / MUSCLE / Clustal / MMseqs2
- Compare orthologs at a functional site / domain
- Pair with `phylotree` for tree + alignment side-by-side

### RDKit — 2D small-molecule depictions

Render 2D depictions of small molecules from SMILES / MOL block using
RDKit-JS (WebAssembly build). Complements `molstar` (3D macromolecules)
— RDKit is the canonical 2D view for drugs, metabolites, organics.

**Skill file**: [rdkit/rdkit.md](./rdkit/rdkit.md)

**When to use**:
- A SMILES (or list) to view as a 2D structure
- Pull drugs / metabolites from ChEMBL / PubChem / DrugBank → render
- Display the substrate / product of a reaction
- Highlight substructures or specific atoms on a molecule

### Phylotree — phylogenetic trees

Open a phylotree.js view for an interactive phylogenetic tree from a
Newick string. Linear or radial layout, branch-length-scaled; rerooting,
ladderise, clade collapse built in.

**Skill file**: [phylotree/phylotree.md](./phylotree/phylotree.md)

**When to use**:
- Show a phylogeny from IQ-TREE / RAxML / FastTree / MrBayes / BEAST
- Inspect / collapse / reroot a clade interactively
- Pair with `msa` for tree + alignment side-by-side

### Generate a custom LiveView app

Write your own interactive component with the LiveView SDK when no existing
viewer fits — a bespoke dashboard, custom plot, or tailored data view that
the agent can still open, drive, and observe.

**Skill file**: [live-view-app.md](./live-view-app.md)

**When to use**:
- The data / interaction doesn't match a ready-made viewer
- You need a tailored view of analysis output
- You want a custom interactive control surface for the user

## The `live_view` tools

| Tool | Purpose |
|------|---------|
| `open_live_view(view_type, title, state, module_url?)` | Open a viewer plugin (e.g. `view_type="vitessce"`) or a custom component (`view_type="custom"` + `module_url`); returns `view_id` |
| `serve_local_data(path)` | Expose a workspace file/dir over HTTP+CORS; returns a fetchable URL |
| `live_view_update(view_id, patch)` | Deep-merge a partial-state patch (drive it) |
| `live_view_set_state(view_id, state)` | Replace the whole state |
| `live_view_get_state(view_id)` | Read state, `status`, and `diagnostics` — incl. the user's own edits |
| `live_view_call(view_id, action, args)` | Invoke a component-defined action |
| `live_view_screenshot(view_id)` | Render the view to an image — `observe_images` it to see it |
| `list_live_views()` / `close_live_view(view_id)` | List / close |

Workflow: `open_live_view` → **verify** (`live_view_get_state` for
`diagnostics`, `live_view_screenshot` to see it — `status: ready` does NOT
mean it rendered correctly) → drive with `live_view_update` →
`live_view_get_state` before the next move. Never treat reading back your
own `live_view_update` value as verification.
