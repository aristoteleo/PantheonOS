---
id: virtualembryo
name: Virtual Embryo — atlas data + knowledge graph
description: |
  Query the Virtual Embryo knowledge graph (mouse/human developmental biology:
  genes, anatomy, Theiler/Carnegie stages, gene expression, diseases, papers)
  and its 3D atlas catalog (anatomical OPT/light-sheet volumes + 3D spatial-
  transcriptomics datasets), and visualise those datasets in 3D with the
  volume3d / spatial3d live-view viewers. Public read-only HTTP API at
  https://kg.virtualembryo.ai — no auth, no key needed for reads. Use when the
  user asks about mouse/human embryo development, where a gene is expressed, an
  anatomical structure, a developmental stage, or wants to see/visualise the
  Virtual Embryo atlas or spatial-transcriptomics data.
tags: [virtual-embryo, knowledge-graph, spatial-transcriptomics, mouse-embryo, gene-expression, anatomy, developmental-biology, neo4j, atlas]
---

# Virtual Embryo — atlas data + knowledge graph

[Virtual Embryo](https://virtualembryo.ai) is an AI-augmented, multi-modal atlas
of **mouse (and human) embryonic development** built in the Xiaojie Qiu lab. It
unifies, under one anatomical reference frame: a **knowledge graph** (genes ·
anatomy · developmental stages · expression · disease · drugs · papers, ~1.8M
facts in Neo4j) and a **3D data atlas** (eMouseAtlas OPT/histology reference
volumes + anatomy meshes, and 3D spatial-transcriptomics reconstructions).

This skill lets you **answer developmental-biology questions from the KG** and
**pull atlas datasets and render them in 3D** with the `volume3d` / `spatial3d`
LiveView viewers.

## The public interfaces (read-only, no key)

| What | URL | Use |
|---|---|---|
| **KG API** | `https://kg.virtualembryo.ai/kg/*` | query genes / anatomy / expression / papers (server-side HTTP) |
| **Catalog** | `https://kg.virtualembryo.ai/index.json` | list samples (volumes) + spatial datasets |
| **Data** | `https://tiles.virtualembryo.org/<path>` | the actual zarr / OME-NGFF stores (read server-side; see "Visualise") |

All reads are open (no auth). The four **write** endpoints (`/kg/cypher_write`,
`/kg/submit_extraction`, …) need an admin key and are not for general use.
Quick health check: `curl https://kg.virtualembryo.ai/healthz` → `{"ok":true}`.

---

## 1. Knowledge graph — query the developmental KG

Hit the endpoints with plain HTTP (`requests`). All return JSON.

```python
import requests
KG = "https://kg.virtualembryo.ai/kg"

# Resolve a name → canonical entity (gene / anatomy / stage / disease …)
hits = requests.get(f"{KG}/search", params={"q": "Sox2", "limit": 5}).json()
# → {"results":[{"iri":"http://identifiers.org/mgi/MGI:98364","label":"Sox2",
#                "type":".../Gene","match_kind":"exact"}]}
iri = hits["results"][0]["iri"]

# One-hop details (type, synonyms, direct relations)
requests.get(f"{KG}/entity", params={"iri": iri}).json()

# Where is a gene expressed? (curated anatomy × stage, from paper extractions)
requests.get(f"{KG}/expression", params={"gene": "Sox2", "stage": "TS17"}).json()
# → {"expressions":[{"anatomy_label":"neural tube","stage":"TS17",
#                    "intensity":...,"paper_doi":...,"confidence":...}], ...}

# Neighbours of an entity, optionally one relation type
requests.get(f"{KG}/expand", params={"iri": iri, "rel": "EXPRESSED_IN"}).json()

# A subgraph for reasoning / a graph view (anchor on a stage or seed entity)
requests.get(f"{KG}/subgraph", params={"stage": "TS17", "limit": 200}).json()
requests.get(f"{KG}/subgraph", params={"seed": iri, "limit": 100}).json()

# Read-only Cypher escape hatch (write keywords are blocked server-side)
requests.post(f"{KG}/cypher", json={
    "cypher": "MATCH (g:Gene)-[:EXPRESSED_IN]->(a:Anatomy {name:'neural tube'}) "
              "RETURN DISTINCT g.name LIMIT 50"}).json()
```

**Endpoints** (all `GET` unless noted): `search(q,limit,species)` ·
`entity(iri)` · `expand(iri,rel,limit)` · `subgraph(stage|seed|type|curated|
staging,limit)` · `expression(gene,stage,limit)` · `dataset(id)` ·
`schema_graph` · `stats` · `resolve_entity(text,type)` ·
`known_papers` · `discover/pubmed(q,year_from)` · `discover/biorxiv(days_back,
category)` · `POST cypher(cypher,params,limit)`.

### KG schema (so you can write meaningful queries)

**Node labels** (every node also has `:Entity`; key props `iri`, `name`,
`synonyms`, `species`):

| Label | What | IRI prefix |
|---|---|---|
| `Gene` | MGI mouse genes (+ human) | `identifiers.org/mgi/MGI:` |
| `Anatomy` | EMAPA (mouse dev) / UBERON | `obo/EMAPA_`, `obo/UBERON_` |
| `CellType` | Cell Ontology | `obo/CL_` |
| `Stage` | Theiler `TS9–TS27` / Carnegie `CS01–CS23` / `PCW…` | `…/ontology/stage/` |
| `Disease` | MONDO / DOID / OMIM | `obo/MONDO_`, … |
| `Phenotype` | HP / MP | `obo/HP_`, `obo/MP_` |
| `Drug` | ChEBI / CTD | `obo/CHEBI_` |
| `Dataset` `Sample` `Plate` `Assay` `Cluster` | atlas catalog entities | `…/ontology/<kind>/` |
| `Paper` | literature | `doi.org/` |

**Relationship types**: `EXPRESSED_IN` (Gene→Anatomy/CellType, with
`qual_at_stage`, `qual_intensity`), `MARKER_FOR`, `REGULATES`,
`CO_EXPRESSED_WITH`, `PART_OF`/`SUBCLASS_OF`/`DEVELOPS_FROM` (anatomy
hierarchy), `AT_STAGE`/`COVERS_STAGES`, `IN_DATASET`/`USES_ASSAY`,
`DESCRIBED_IN` (→Paper), `CAUSES_PHENOTYPE`/`CAUSES_DISEASE`/`DISEASE_MODEL_OF`,
`EQUIVALENT_TO` (cross-vocab). Edges carry `paper_doi`, `confidence`,
`evidence_span`, `qual_at_stage`.

Stages: mouse **Theiler TS9–TS27**, human **Carnegie CS01–CS23** / **PCW**.
Rough mouse age map: E7.5≈TS11, E8.5≈TS13, E9.5≈TS15, E10.5≈TS17, E11.5≈TS19,
E12.5≈TS20, E13.5≈TS21, E14.5≈TS23 (use `search`/`resolve_entity` to confirm).

---

## 2. Atlas catalog — find datasets to visualise

The KG describes facts; the **renderable data** is listed in the catalog.

```python
import requests
cat = requests.get("https://kg.virtualembryo.ai/index.json").json()
cat.keys()  # stages, samples, spatial_datasets, singlecell_datasets, *_by_stage
```

- **`samples`** (72) — eMouseAtlas reference **volumes** + anatomy meshes.
  Fields: `ema_code`, `theiler_stage`, `voxel_um`, **`zarr_path`** (OME-NGFF
  volume, e.g. `samples/ema10/images/reference.ome.zarr`), `mesh_glb_path`,
  `has_anatomy_mesh`. → render with **`volume3d`**.
- **`spatial_datasets`** (64) — 3D / 2D **spatial transcriptomics** (Stereo-seq
  MOSTA sections, Spateo 3D reconstructions, digital embryos). Fields: `name`,
  `species`, `stage_xref`, `n_cells`, `n_genes`, `technology`, `spatial_ndim`
  (2 or 3), `default_color_obs`, `default_spatial_key`, **`path`** (the
  `.spatial.zarr`, e.g. `datasets/spatial/digiembryo_e7_5_rep1.spatial.zarr`),
  `paper_doi`. → render with **`spatial3d`**.

Filter the catalog in code, e.g. 3D spatial datasets for E9.5:
```python
e95 = [s for s in cat["spatial_datasets"]
       if s["spatial_ndim"] == 3 and "e9" in s["name"].lower()]
```
`https://kg.virtualembryo.ai/kg/stats` gives headline counts (80k genes, 191
datasets, …).

---

## 3. Visualise an atlas dataset in 3D

The viewers (`volume3d`, `spatial3d`) load a zarr the **LiveView data server**
serves with CORS. The public data host (`tiles.virtualembryo.org`) has **no
CORS**, so the browser can't fetch it directly — **read it server-side and
re-serve it locally**. (On a machine that already has the atlas checked out
locally, just `serve_local_data` the local path and skip the fetch.)

### Spatial transcriptomics → `spatial3d`

A VE `.spatial.zarr` is already in spatial3d's format. Read it over HTTP with
zarrita/zarr (no CORS needed server-side), write a local copy, serve, open.
See the **spatial3d** skill for the full viewer + the `write_spatial_zarr`
recipe; this fetches the source for it:

```python
import json, requests, numpy as np, zarr, anndata as ad, pandas as pd
from scipy.sparse import csr_matrix

def fetch_ve_spatial(name, out="/workspace", max_cells=150_000):
    """Pull a VE spatial dataset → an AnnData (subsampled if huge)."""
    cat = requests.get("https://kg.virtualembryo.ai/index.json").json()
    rec = next(s for s in cat["spatial_datasets"] if s["name"] == name)
    url = f"https://tiles.virtualembryo.org/{rec['path']}"
    g = zarr.open_group(url, mode="r")              # remote read, no CORS needed
    N = rec["n_cells"]
    idx = np.arange(N)
    if N > max_cells:                               # subsample big reconstructions
        idx = np.sort(np.random.default_rng(0).choice(N, max_cells, replace=False))
    coords = np.asarray(g[f"obsm/{rec['default_spatial_key']}"][:])[idx].astype("float32")
    co = rec["default_color_obs"]
    codes = np.asarray(g[f"obs/{co}"][:])[idx]
    cats = list(g[f"obs/{co}"].attrs["categories"])
    X = csr_matrix((np.asarray(g["X/data"][:]), np.asarray(g["X/indices"][:]),
                    np.asarray(g["X/indptr"][:])), shape=(N, rec["n_genes"]))[idx]
    genes = requests.get(f"{url}/gene_symbols.json").json()
    a = ad.AnnData(X=X, obs=pd.DataFrame({"cell_type": pd.Categorical.from_codes(
        np.clip(codes, -1, len(cats) - 1), categories=cats)}), var=pd.DataFrame(index=genes))
    a.obsm["spatial"] = coords
    try:
        a.obsm["X_umap"] = np.asarray(g["obsm/X_umap"][:])[idx].astype("float32")
    except Exception:
        pass
    a.write_h5ad(f"{out}/{name}.h5ad")
    return f"{out}/{name}.h5ad"

# then: write_spatial_zarr(anndata.read_h5ad(path), out_zarr)  # from spatial3d.md
#       url = serve_local_data(out_zarr)
#       open_live_view(view_type="spatial3d", state={"url": url, "colorBy":"cluster"})
```

Note: big reconstructions (`e9_5_embryo` 646k, `e11_5_embryo` 7M cells) are slow
to pull whole over HTTP — keep `max_cells` modest, or prefer a `digiembryo_*`
(3D, ~8–27k cells) or a MOSTA section for a quick render.

### Reference volume → `volume3d`

```python
import requests, numpy as np, zarr
def fetch_ve_volume(ema_code, out="/workspace"):
    cat = requests.get("https://kg.virtualembryo.ai/index.json").json()
    rec = next(s for s in cat["samples"] if s["ema_code"].lower() == ema_code.lower())
    g = zarr.open_group(f"https://tiles.virtualembryo.org/{rec['zarr_path']}", mode="r")
    vol = np.asarray(g["0"][:])     # finest level [Z,Y,X]; coarsen if very large
    # then write_ome_zarr_v2(vol, out_zarr, voxel=rec.get("voxel_um", (1,1,1)))  # volume3d.md
    return vol
# open_live_view(view_type="volume3d", state={"url": serve_local_data(out_zarr), "mode":"iso"})
```

---

## 4. End-to-end examples

**"What is Sox2 and where is it expressed in the mouse embryo?"**
`search("Sox2")` → IRI → `entity(iri)` (what it is) → `expression(gene="Sox2")`
(anatomy × stage rows). Summarise the expression domains + cite `paper_doi`.

**"Show me an E9.5 mouse embryo in 3D coloured by cell type, then by Sox2."**
Pick a 3D dataset near E9.5 from `spatial_datasets` (e.g. a `digiembryo_*` or
`e9_5_embryo`) → `fetch_ve_spatial` → `write_spatial_zarr` → `serve_local_data`
→ `open_live_view("spatial3d", state={url, colorBy:"cluster"})` → then
`live_view_update(view_id, {"colorBy":"gene","gene":"Sox2","colormap":"plasma"})`.

**"Which genes are expressed in the neural tube?"**
`resolve_entity("neural tube", type="anatomy")` → EMAPA IRI → `POST cypher`
`MATCH (g:Gene)-[:EXPRESSED_IN]->(a:Anatomy {iri:$iri}) RETURN DISTINCT g.name`
(params `{iri}`), or include sub-structures with `-[:PART_OF*0..2]->`.

**"Render the EMA10 reference embryo volume."**
`fetch_ve_volume("EMA10")` → `write_ome_zarr_v2` → `serve_local_data` →
`open_live_view("volume3d", state={url, mode:"iso"})`.

## Gotchas

- **Data host has no CORS** — never give a `tiles.virtualembryo.org` URL
  straight to a viewer; read it server-side (zarr/requests) and `serve_local_data`
  a local copy (which adds CORS + Range). The KG API host *does* send CORS but
  you call it server-side anyway.
- **KG ≠ raw matrices.** `/kg/expression` returns *curated* anatomy×stage
  annotations (with paper provenance), not an expression matrix. Quantitative
  per-cell expression lives in the dataset `.spatial.zarr` (the viewer reads it).
- **Stages**: the KG keys on Theiler/Carnegie codes, not "E9.5" — map the age to
  a stage code (or `search` the stage) before filtering.
- **Big datasets**: subsample when fetching 3D reconstructions; the viewer also
  strides rendering, but the HTTP pull is the slow part.
- An MCP server with the same read tools also exists at
  `https://kg.virtualembryo.ai/mcp` (streamable-HTTP) if a `ve-curator` MCP
  profile is configured — but the HTTP calls above need no setup.
