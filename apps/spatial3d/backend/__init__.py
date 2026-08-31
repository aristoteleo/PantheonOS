"""Spatial 3D's backend: AnnData in, the viewer's spatial Zarr out.

The frontend reads a bespoke layout — ``obsm/spatial``, ``obs/cell_type``
(coded, categories in attrs), optional ``obsm/X_umap``, optional CSC
expression (``X_csc/{data,indices,indptr}`` + ``gene_symbols.json``), and a
``_spatial.json`` manifest. That recipe ships in the skill for the agent to
run by hand; here it IS the backend, so a desktop double-click on an .h5ad
and the Data menu's example both work without an agent in the loop.

``prepare(path)`` converts (workspace-cached by mtime, original untouched);
``example()`` synthesises a small tissue — four cell types arranged in
gaussian blobs and a ring, marker genes elevated per type so gene colouring
has something to show — and feeds it through the same writer.
"""

from pathlib import Path
import hashlib
import json
import urllib.request

CACHE_DIR = ".pantheon/atrium-spatial3d"
CACHE_VERSION = "v1"

# The viewer's home project: this dataset on the Virtual Embryo R2 bucket is
# ALREADY in the viewer's native spatial-zarr layout — the format came from
# there. E8.0 whole mouse embryo, 11k cells, Stereo-seq (Xie et al., Cell
# 2025, the digital embryo).
TILES = "https://tiles.virtualembryo.org/datasets/spatial"

# Curated off the Virtual Embryo bucket — every entry is already in this
# viewer's native spatial-zarr layout. digiembryo: Xie et al., Cell 2025;
# E11.5: Stereo-seq organ/embryo maps.
DATASETS = [
    {"id": "digiembryo_e7_5", "label": "Digital embryo E7.5 — 13k cells",
     "path": "digiembryo_e7_5_rep1.spatial.zarr",
     "title": "Digital embryo E7.5 — Stereo-seq (Xie et al., Cell 2025)"},
    {"id": "digiembryo_e7_75", "label": "Digital embryo E7.75 — 21k cells",
     "path": "digiembryo_e7_75_rep1.spatial.zarr",
     "title": "Digital embryo E7.75 — Stereo-seq (Xie et al., Cell 2025)"},
    {"id": "digiembryo_e8_0", "label": "Digital embryo E8.0 — 11k cells",
     "path": "digiembryo_e8_0_rep1.spatial.zarr",
     "title": "Digital embryo E8.0 — Stereo-seq (Xie et al., Cell 2025)"},
    {"id": "e11_5_heart", "label": "E11.5 heart — 99k cells",
     "path": "e11_5_heart.spatial.zarr",
     "title": "E11.5 heart — Stereo-seq, 98,966 cells"},
    {"id": "e11_5_embryo", "label": "E11.5 whole embryo — 7.0M cells (heavy)",
     "path": "e11_5_embryo.spatial.zarr",
     "title": "E11.5 whole embryo — Stereo-seq, 6,993,667 cells"},
]
DEFAULT_ID = "digiembryo_e8_0"


def _reachable(url: str, timeout: float = 6.0) -> bool:
    # A browser-ish User-Agent, or Cloudflare's bot fight answers this probe
    # 403 — the pod's default Python-urllib UA is on the block list. The
    # actual data reads come from the browser and were never affected.
    try:
        req = urllib.request.Request(
            url, method="HEAD",
            headers={"User-Agent": "Mozilla/5.0 (PantheonOS Atrium)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return 200 <= res.status < 400
    except Exception:
        return False
SPATIAL_KEYS = ("spatial", "spatial_3d", "x_spatial", "spatial3d", "x_spatial_3d")


def _write_spatial_zarr(adata, out_dir: Path, spatial_key=None, cluster_key=None) -> dict:
    """AnnData → the viewer's spatial Zarr (v2, zarr 2.x API). Returns state hints."""
    import numpy as np
    import zarr
    from scipy.sparse import csc_matrix

    n = adata.n_obs
    if spatial_key is None:
        cand = [k for k in adata.obsm if k.lower() in SPATIAL_KEYS]
        three = [k for k in cand if np.asarray(adata.obsm[k]).shape[1] >= 3]
        pool = three or cand or list(adata.obsm)
        if not pool:
            raise ValueError("no obsm embedding to use as spatial coordinates")
        spatial_key = pool[0]
    coords = np.asarray(adata.obsm[spatial_key], dtype="float32")[:, :3]
    ndim = coords.shape[1]

    if cluster_key is None:
        for c in adata.obs.columns:
            if str(adata.obs[c].dtype) in ("category", "object"):
                cluster_key = c
                break
        cluster_key = cluster_key or (adata.obs.columns[0] if len(adata.obs.columns) else None)
    if cluster_key is None:
        adata.obs["cell_type"] = "cell"
        cluster_key = "cell_type"
    col = adata.obs[cluster_key].astype("category")
    codes = col.cat.codes.to_numpy().astype("int32")
    cats = list(map(str, col.cat.categories))

    # zarr 2.x on the pod today; the 3.x API differs, so write through a shim
    # that produces v2 either way (broadest zarrita compatibility).
    if hasattr(zarr, "DirectoryStore"):
        root = zarr.open_group(store=zarr.DirectoryStore(str(out_dir)), mode="w")
    else:
        from zarr.storage import LocalStore
        root = zarr.open_group(store=LocalStore(str(out_dir)), mode="w", zarr_format=2)

    def put(name, data, chunks):
        try:
            return root.create_dataset(name, data=data, chunks=chunks)
        except TypeError:
            a = root.create_array(name, shape=data.shape, dtype=data.dtype, chunks=chunks)
            a[...] = data
            return a

    put("obsm/spatial", coords, (n, ndim))
    ct = put("obs/cell_type", codes, (n,))
    ct.attrs["categories"] = cats

    obsm_meta = {"spatial": {"shape": [n, ndim]}}
    if "X_umap" in adata.obsm:
        um = np.asarray(adata.obsm["X_umap"], dtype="float32")[:, :2]
        put("obsm/X_umap", um, (n, 2))
        obsm_meta["X_umap"] = {"shape": [n, 2]}

    has_expr = False
    if adata.n_vars:
        x = csc_matrix(adata.X)
        put("X_csc/data", x.data.astype("float32"), (min(65536, max(x.data.size, 1)),))
        put("X_csc/indices", x.indices.astype("int32"), (min(65536, max(x.indices.size, 1)),))
        put("X_csc/indptr", x.indptr.astype("int32"), (x.indptr.size,))
        (out_dir / "gene_symbols.json").write_text(json.dumps(list(map(str, adata.var_names))))
        has_expr = True

    (out_dir / "_spatial.json").write_text(json.dumps({
        "n_cells": int(n), "n_genes": int(adata.n_vars),
        "default_spatial_key": "spatial", "spatial_ndim": int(ndim),
        "default_color_obs": "cell_type",
        "obs": {"cell_type": {"kind": "categorical", "n_categories": len(cats)}},
        "obsm": obsm_meta, "has_expression": has_expr,
    }))
    return {"n_cells": int(n), "ndim": int(ndim)}


def _cache_dir(workspace: Path, key: str) -> Path:
    out = workspace / CACHE_DIR / key / "cells.spatial.zarr"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _synthetic_adata():
    """A small tissue: four cell types, marker genes, honest 3D structure."""
    import anndata as ad
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(11)
    blobs = {
        "Neuron": (rng.normal([0, 0, 0], [18, 18, 10], size=(900, 3))),
        "Glia": (rng.normal([45, 10, 5], [12, 14, 8], size=(700, 3))),
        "Endothelial": None,  # a vessel ring, built below
        "Immune": (rng.normal([20, 45, -8], [10, 10, 6], size=(400, 3))),
    }
    theta = rng.uniform(0, 2 * np.pi, 400)
    ring = np.c_[22 + 30 * np.cos(theta), 20 + 30 * np.sin(theta), rng.normal(0, 4, 400)]
    blobs["Endothelial"] = ring + rng.normal(0, 2.2, ring.shape)

    coords = np.vstack(list(blobs.values())).astype("float32")
    labels = np.repeat(list(blobs), [len(v) for v in blobs.values()])
    n = len(coords)

    genes = [f"{t[:4]}_{i}" for t in blobs for i in range(6)]
    x = rng.gamma(1.2, 0.6, size=(n, len(genes))).astype("float32")
    for ti, t in enumerate(blobs):
        mask = labels == t
        x[np.ix_(mask, np.arange(ti * 6, ti * 6 + 6))] += rng.gamma(4.0, 1.2, (mask.sum(), 6))

    return ad.AnnData(
        X=x,
        obs=pd.DataFrame({"cell_type": pd.Categorical(labels)},
                         index=[f"cell{i}" for i in range(n)]),
        var=pd.DataFrame(index=genes),
        obsm={"spatial": coords},
    )


def register(ctx):
    @ctx.method
    async def prepare(path: str) -> dict:
        """Convert an .h5ad and hand back the viewer state that opens it."""
        import anndata as ad

        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {path}")
        key = hashlib.sha1(
            f"{src}:{src.stat().st_mtime_ns}:{CACHE_VERSION}".encode()
        ).hexdigest()[:20]
        out = _cache_dir(ctx.workspace, key)
        side = out.parent / "meta.json"
        if out.exists() and side.exists():
            meta = json.loads(side.read_text())
        else:
            meta = _write_spatial_zarr(ad.read_h5ad(str(src)), out)
            side.write_text(json.dumps(meta))
        ctx.log(f"prepare {src.name}: {meta['n_cells']} cells, {meta['ndim']}D")
        return {
            "config": {
                "url": await ctx.serve(out),
                "mode": "3d" if meta["ndim"] >= 3 else "2d",
                "colorBy": "cluster",
                "title": src.name,
            }
        }

    async def _synthetic_state() -> dict:
        out = _cache_dir(ctx.workspace, f"example-{CACHE_VERSION}")
        if not (out / "_spatial.json").exists():
            _write_spatial_zarr(_synthetic_adata(), out)
        return {
            "url": await ctx.serve(out),
            "mode": "3d",
            "colorBy": "cluster",
            "title": "Synthetic tissue — 2,400 cells",
        }

    @ctx.method
    async def datasets() -> dict:
        """The Data menu's catalog — ids and labels only."""
        return {"datasets": [{"id": d["id"], "label": d["label"]} for d in DATASETS]}

    @ctx.method
    async def load_dataset(id: str) -> dict:
        """One catalog entry as viewer state; 'synthetic' is always local."""
        if id == "synthetic":
            ctx.log("dataset: synthetic tissue")
            return {"config": await _synthetic_state(), "id": "synthetic"}
        entry = next((d for d in DATASETS if d["id"] == id), None)
        if entry is None:
            raise ValueError(f"unknown dataset '{id}'")
        url = f"{TILES}/{entry['path']}"
        if not _reachable(f"{url}/_spatial.json"):
            raise RuntimeError("the Virtual Embryo bucket is unreachable from this pod")
        ctx.log(f"dataset: {entry['label']}")
        return {
            "config": {"url": url, "mode": "3d", "colorBy": "cluster", "title": entry["title"]},
            "id": id,
        }

    @ctx.method
    async def example() -> dict:
        """Load Example Data — the default atlas entry, synthetic offline."""
        try:
            return await load_dataset(DEFAULT_ID)
        except Exception:
            ctx.log("example: atlas unreachable — synthetic tissue served")
            return {"config": await _synthetic_state(), "id": "synthetic"}
