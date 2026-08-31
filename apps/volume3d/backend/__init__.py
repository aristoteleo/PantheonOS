"""Volume 3D's backend: the Virtual Embryo atlas volume, synthetic fallback.

This viewer came from the Virtual Embryo project, and its example comes home:
the eMouseAtlas EMA10 reference volume served off the project's R2 bucket
(tiles.virtualembryo.org — CORS opened for GET/HEAD + Range, which zarrita's
chunk reads preflight). The pod checks reachability first and falls back to a
small synthesised OME-NGFF volume in the workspace cache, so the example
still works offline.
"""

from pathlib import Path
import urllib.request

CACHE_DIR = ".pantheon/atrium-volume3d"
CACHE_VERSION = "v1"

TILES = "https://tiles.virtualembryo.org/samples"

# The eMouseAtlas reference-volume ladder, one embryo per Theiler stage.
DATASETS = [
    {"id": "ema10", "label": "TS10 (≈E7.0) — ema10"},
    {"id": "ema17", "label": "TS11 (≈E7.5) — ema17"},
    {"id": "ema21", "label": "TS12 (≈E8.0) — ema21"},
    {"id": "ema24", "label": "TS13 (≈E8.5) — ema24"},
    {"id": "ema27", "label": "TS14 (≈E9.0) — ema27"},
    {"id": "ema28", "label": "TS15 (≈E9.5) — ema28"},
    {"id": "ema41", "label": "TS16 (≈E10) — ema41"},
    {"id": "ema49", "label": "TS17 (≈E10.5) — ema49"},
]
DEFAULT_ID = "ema10"


def _url(sample: str) -> str:
    return f"{TILES}/{sample}/images/reference.ome.zarr"


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


def _write_synthetic_ngff(out_dir: Path) -> None:
    """Two nuclei and a hollow shell — enough structure for MIP and ISO."""
    import numpy as np
    import zarr

    n = 96
    z, y, x = np.mgrid[0:n, 0:n, 0:n].astype("float32")

    def ball(cz, cy, cx, r):
        return np.exp(-(((z - cz) ** 2 + (y - cy) ** 2 + (x - cx) ** 2) / (2 * r * r)))

    vol = 0.9 * ball(34, 38, 36, 11) + 0.75 * ball(60, 58, 62, 9)
    d = np.sqrt((z - 48) ** 2 + (y - 48) ** 2 + (x - 48) ** 2)
    vol += 0.5 * np.exp(-((d - 34) ** 2) / (2 * 2.5 ** 2))  # the shell
    vol += np.random.default_rng(5).normal(0, 0.015, vol.shape).astype("float32")
    vol = np.clip(vol, 0, 1).astype("float32")

    if hasattr(zarr, "DirectoryStore"):
        root = zarr.open_group(store=zarr.DirectoryStore(str(out_dir)), mode="w")
    else:
        from zarr.storage import LocalStore
        root = zarr.open_group(store=LocalStore(str(out_dir)), mode="w", zarr_format=2)
    try:
        root.create_dataset("0", data=vol, chunks=(48, 48, 48))
    except TypeError:
        a = root.create_array("0", shape=vol.shape, dtype=vol.dtype, chunks=(48, 48, 48))
        a[...] = vol
    root.attrs["multiscales"] = [{
        "version": "0.4",
        "name": "synthetic",
        "axes": [
            {"name": "z", "type": "space"},
            {"name": "y", "type": "space"},
            {"name": "x", "type": "space"},
        ],
        "datasets": [{
            "path": "0",
            "coordinateTransformations": [{"type": "scale", "scale": [1.0, 1.0, 1.0]}],
        }],
    }]


def register(ctx):
    async def _synthetic_state() -> dict:
        out = ctx.workspace / CACHE_DIR / f"example-{CACHE_VERSION}" / "volume.ome.zarr"
        if not (out / ".zgroup").exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            _write_synthetic_ngff(out)
        return {
            "url": await ctx.serve(out),
            "mode": "iso",
            "threshold": 0.35,
            "title": "Synthetic volume — two nuclei and a shell",
        }

    @ctx.method
    async def datasets() -> dict:
        """The Data menu's catalog — ids and labels only."""
        return {"datasets": [{"id": d["id"], "label": d["label"]} for d in DATASETS]}

    @ctx.method
    async def load_dataset(id: str) -> dict:
        """One stage's reference volume; 'synthetic' is always local."""
        if id == "synthetic":
            ctx.log("dataset: synthetic volume")
            return {"config": await _synthetic_state(), "id": "synthetic"}
        entry = next((d for d in DATASETS if d["id"] == id), None)
        if entry is None:
            raise ValueError(f"unknown dataset '{id}'")
        url = _url(id)
        if not _reachable(f"{url}/.zattrs"):
            raise RuntimeError("the Virtual Embryo bucket is unreachable from this pod")
        ctx.log(f"dataset: {entry['label']}")
        return {
            "config": {
                "url": url,
                "mode": "iso",
                "title": f"eMouseAtlas {entry['label'].replace(' — ', ' · ')} — reference volume",
            },
            "id": id,
        }

    @ctx.method
    async def example() -> dict:
        """Load Example Data — the default stage, synthetic offline."""
        try:
            return await load_dataset(DEFAULT_ID)
        except Exception:
            ctx.log("example: atlas unreachable — synthetic volume served")
            return {"config": await _synthetic_state(), "id": "synthetic"}
