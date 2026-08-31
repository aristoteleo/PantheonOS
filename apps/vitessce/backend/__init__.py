"""Vitessce's backend: making any .h5ad openable, in Vitessce's own process.

Vitessce's JS reads AnnData as zarr over HTTP — it cannot open a raw HDF5
.h5ad. So `prepare(path)` rewrites the file as an ``anndata.zarr`` store in
the workspace cache, serves the *directory* (the data server hands out every
chunk under a served root by relative URL — the same mechanism Viv's
``.ome.zarr`` path rides), and generates a Vitessce view config from what the
file actually contains:

- every 2D-usable ``obsm`` matrix becomes an embedding (``X_umap`` → UMAP,
  ``spatial*`` → SPATIAL with dims [0, 1], anything else by its own name);
- every small categorical ``obs`` column becomes a cell-set grouping;
- ``X`` becomes the feature matrix, densified — Vitessce's sparse support is
  narrower than anndata's — and gene-capped when that would balloon.

The config, not the data, is what the frontend receives: the adapter's state
IS a Vitessce config, so file-open and agent-driven views stay one code path.
"""

from pathlib import Path
import hashlib
import json
import re
import shutil

CACHE_DIR = ".pantheon/atrium-vitessce"
CACHE_VERSION = "v1"
# Densifying X is what Vitessce reads most reliably; cap the gene axis so a
# 100k×30k sparse atlas cannot balloon into gigabytes of dense zarr.
MAX_DENSE_CELLS_X_GENES = 60_000_000
MAX_GENES_WHEN_CAPPED = 2000

EMBEDDING_NAMES = {
    "x_umap": "UMAP", "umap": "UMAP",
    "x_pca": "PCA", "pca": "PCA",
    "x_tsne": "t-SNE", "tsne": "t-SNE",
    "x_draw_graph_fa": "FA",
}


def _pretty(col: str) -> str:
    return re.sub(r"[_\-]+", " ", str(col)).strip().title() or str(col)


def _embedding_type(key: str) -> str:
    low = key.lower()
    if low in EMBEDDING_NAMES:
        return EMBEDDING_NAMES[low]
    if "spatial" in low:
        return "SPATIAL"
    return re.sub(r"^x[_\-]", "", low).upper() or key.upper()


def _convert(src: Path, workspace: Path) -> dict:
    """Rewrite ``src`` as anndata.zarr + a config skeleton; cached by mtime."""
    import anndata as ad
    import numpy as np
    import scipy.sparse as sp

    cache = workspace / CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{src}:{src.stat().st_mtime_ns}:{CACHE_VERSION}".encode()
    ).hexdigest()[:20]
    dest = cache / key / "adata.zarr"
    side = cache / key / "config.json"

    if dest.exists() and side.exists():
        try:
            skeleton = json.loads(side.read_text())
            skeleton["zarr_path"] = str(dest)
            skeleton["cached"] = True
            return skeleton
        except Exception:
            pass  # unreadable sidecar — redo the conversion

    adata = ad.read_h5ad(str(src))
    n_obs, n_vars = adata.shape

    # ── X: dense float32, gene-capped when dense would balloon ─────────────
    note = None
    if n_obs * n_vars > MAX_DENSE_CELLS_X_GENES and n_vars > MAX_GENES_WHEN_CAPPED:
        if "highly_variable" in adata.var and bool(adata.var["highly_variable"].any()):
            mask = adata.var["highly_variable"].to_numpy()
            adata = adata[:, mask].copy()
            if adata.shape[1] > MAX_GENES_WHEN_CAPPED:
                adata = adata[:, :MAX_GENES_WHEN_CAPPED].copy()
            note = f"showing {adata.shape[1]} highly-variable of {n_vars} genes"
        else:
            X = adata.X
            var_across = (
                np.asarray(X.power(2).mean(axis=0)).ravel() - np.asarray(X.mean(axis=0)).ravel() ** 2
                if sp.issparse(X)
                else np.nanvar(np.asarray(X), axis=0)
            )
            top = np.argsort(var_across)[::-1][:MAX_GENES_WHEN_CAPPED]
            adata = adata[:, np.sort(top)].copy()
            note = f"showing top {adata.shape[1]} variable of {n_vars} genes"

    X = adata.X
    if X is None:
        X = np.zeros(adata.shape, dtype="float32")
    if sp.issparse(X):
        X = np.asarray(X.todense())
    X = np.asarray(X, dtype="float32")

    # ── embeddings: any obsm matrix with ≥2 numeric columns ────────────────
    embeddings = []
    obsm_out = {}
    for obsm_key in list(adata.obsm.keys()):
        try:
            m = np.asarray(adata.obsm[obsm_key], dtype="float32")
        except Exception:
            continue
        if m.ndim != 2 or m.shape[1] < 2 or not np.isfinite(m[:1]).all():
            continue
        obsm_out[obsm_key] = m
        embeddings.append({
            "path": f"obsm/{obsm_key}",
            "dims": [0, 1],
            "embeddingType": _embedding_type(obsm_key),
        })

    # ── cell sets: small categorical / string obs columns ──────────────────
    obs_sets = []
    for col in adata.obs.columns:
        s = adata.obs[col]
        if s.dtype.name not in ("category", "object", "string", "bool"):
            continue
        n = int(s.astype("string").nunique(dropna=True))
        if 1 < n <= 100:
            obs_sets.append({"name": _pretty(col), "path": f"obs/{col}"})

    slim = ad.AnnData(X=X, obs=adata.obs.copy(), var=adata.var.copy(), obsm=obsm_out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    chunk = (min(slim.shape[0], 1000) or 1, min(slim.shape[1], 256) or 1)
    slim.write_zarr(str(dest), chunks=chunk)

    skeleton = {
        "embeddings": embeddings,
        "obs_sets": obs_sets,
        "n_obs": int(slim.shape[0]),
        "n_vars": int(slim.shape[1]),
        "note": note,
        "zarr_path": str(dest),
    }
    try:
        side.write_text(json.dumps({k: v for k, v in skeleton.items() if k != "zarr_path"}))
    except Exception:
        pass  # missing sidecar only costs a redo
    return skeleton


def _build_config(name: str, url: str, sk: dict) -> dict:
    options: dict = {"obsFeatureMatrix": {"path": "X"}}
    if sk["embeddings"]:
        options["obsEmbedding"] = sk["embeddings"]
    if sk["obs_sets"]:
        options["obsSets"] = sk["obs_sets"]

    # Vitessce sizes rows as height/totalRows — a layout must total 12 rows
    # or it fills only part of the window (8 rows = two-thirds, then blank).
    layout = []
    if sk["embeddings"]:
        first = sk["embeddings"][0]["embeddingType"]
        layout.append({
            "component": "scatterplot",
            "coordinationScopes": {"embeddingType": "A"},
            "x": 0, "y": 0, "w": 7, "h": 12,
        })
        layout.append({"component": "obsSets", "x": 7, "y": 0, "w": 2, "h": 6})
        layout.append({"component": "featureList", "x": 9, "y": 0, "w": 3, "h": 6})
        layout.append({"component": "heatmap", "x": 7, "y": 6, "w": 5, "h": 6})
    else:
        first = None
        layout.append({"component": "heatmap", "x": 0, "y": 0, "w": 8, "h": 12})
        layout.append({"component": "obsSets", "x": 8, "y": 0, "w": 4, "h": 6})
        layout.append({"component": "featureList", "x": 8, "y": 6, "w": 4, "h": 6})

    title = name if not sk.get("note") else f"{name} ({sk['note']})"
    cfg = {
        "version": "1.0.16",
        "name": title,
        "description": f"{sk['n_obs']} cells x {sk['n_vars']} genes",
        "datasets": [{
            "uid": "A",
            "name": name,
            "files": [{
                "fileType": "anndata.zarr",
                "url": url,
                "options": options,
                "coordinationValues": {
                    "obsType": "cell",
                    "featureType": "gene",
                    "featureValueType": "expression",
                },
            }],
        }],
        "initStrategy": "auto",
        "coordinationSpace": {},
        "layout": layout,
    }
    if first:
        cfg["coordinationSpace"]["embeddingType"] = {"A": first}
    return cfg


def _compute_umap_into_cache(sk: dict) -> dict:
    """Add ``obsm/X_umap`` to the cached zarr store; update the skeleton.

    Computed on the converted matrix (already dense, gene-capped) with the
    standard-lite recipe — normalize, log1p, PCA, neighbors, UMAP — on a
    throwaway copy, so the stored X stays exactly what the file held. The
    original .h5ad is never touched: the embedding lives in the cache, which
    also means every later open of the same file keeps it for free.
    """
    import anndata as ad
    import numpy as np

    zarr_path = Path(sk["zarr_path"])
    a = ad.read_zarr(str(zarr_path))
    if "X_umap" not in a.obsm:
        t = a.copy()
        try:
            import scanpy as sc
            sc.pp.normalize_total(t, target_sum=1e4)
            sc.pp.log1p(t)
            sc.pp.pca(t, n_comps=max(2, min(50, min(t.shape) - 1)))
            sc.pp.neighbors(t)
            sc.tl.umap(t)
            emb = t.obsm["X_umap"]
        except ImportError:
            # scanpy absent — sklearn PCA + umap-learn cover the same ground.
            from sklearn.decomposition import PCA
            import umap
            X = np.asarray(t.X, dtype="float32")
            X = np.log1p(1e4 * X / np.maximum(X.sum(axis=1, keepdims=True), 1e-9))
            comps = PCA(n_components=max(2, min(50, min(X.shape) - 1))).fit_transform(X)
            emb = umap.UMAP().fit_transform(comps)
        a.obsm["X_umap"] = np.asarray(emb, dtype="float32")

        tmp = zarr_path.with_name(zarr_path.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        chunk = (min(a.shape[0], 1000) or 1, min(a.shape[1], 256) or 1)
        a.write_zarr(str(tmp), chunks=chunk)
        shutil.rmtree(zarr_path)
        tmp.rename(zarr_path)

    # UMAP first: it becomes the scatterplot the config opens on.
    others = [e for e in sk["embeddings"] if e["embeddingType"] != "UMAP"]
    sk["embeddings"] = [
        {"path": "obsm/X_umap", "dims": [0, 1], "embeddingType": "UMAP"}
    ] + others
    side = zarr_path.parent / "config.json"
    try:
        side.write_text(json.dumps({k: v for k, v in sk.items() if k != "zarr_path"}))
    except Exception:
        pass  # missing sidecar only costs a redo
    return sk


def register(ctx):
    @ctx.method
    async def prepare(path: str) -> dict:
        """A Vitessce view config for ``path`` — zarr conversion included."""
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {path}")

        skeleton = _convert(src, ctx.workspace)
        url = await ctx.serve(Path(skeleton["zarr_path"]))
        config = _build_config(src.name, url, skeleton)
        ctx.log(
            f"prepare {src.name}: {skeleton['n_obs']}x{skeleton['n_vars']}, "
            f"{len(skeleton['embeddings'])} embeddings, "
            f"{len(skeleton['obs_sets'])} set groups, cached={bool(skeleton.get('cached'))}"
        )
        return {"config": config}

    @ctx.method
    async def compute_umap(path: str) -> dict:
        """UMAP for a file that shipped without one — cached, original untouched."""
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {path}")
        skeleton = _compute_umap_into_cache(_convert(src, ctx.workspace))
        url = await ctx.serve(Path(skeleton["zarr_path"]))
        ctx.log(f"compute_umap {src.name}: {skeleton['n_obs']} cells embedded")
        return {"config": _build_config(src.name, url, skeleton)}

    @ctx.method
    async def example() -> dict:
        """The package's bundled demo config — the menu's Load Example Data."""
        demo = Path(__file__).resolve().parent.parent / "demo.json"
        if not demo.is_file():
            raise FileNotFoundError("this package bundles no demo.json")
        return {"config": json.loads(demo.read_text())}
