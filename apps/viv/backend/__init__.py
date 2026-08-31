"""Viv's backend: making any TIFF openable, in Viv's own process.

This is the pod half that used to live in the shell as ~250 lines of Python
inside a TypeScript template string (Atrium's ``network/bioimage.ts``),
executed through the shared Python toolset. Moving it here changes three
things and keeps everything else byte-faithful:

- it is a real module now, not codegen;
- it runs in THIS app's supervised process, so a crash or an OOM kills Viv's
  backend and nothing else — the old version could take the shared kernel
  with it;
- the SystemExit paranoia the old header documents is gone *because the
  process boundary is the fix*: an exit here is a subprocess exiting.

The memory discipline stays. A 39 MB LZW file holds a few hundred megabytes
of pixels unpacked; percentiles are taken on a stride-sampled view and the
rescale runs one channel at a time, because doing either naively is what once
made this dangerous.

The cache directory and key (``.pantheon/atrium-ome``, ``:v3``) are the ones
the shell version used, so every image already converted stays converted.
"""

from pathlib import Path
import gc
import hashlib
import json

# Only powers of two are available as decimation steps, so a cap set too
# tight halves an image that merely grazed it. 10k keeps ordinary microscopy
# intact and still bounds a slide scan.
MAX_BASE_EDGE = 10000

NATIVE = (".ome.tif", ".ome.tiff", ".ome.zarr", ".zarr")


def _rgb_channels(dtype: str = "uint8") -> list:
    """Channel setup for a true-colour image.

    Viv's default fluorescence palette turns a white brightfield background
    lavender and pulls the white balance apart with per-channel stretches.
    R/G/B over the full dtype range reproduces the original colours exactly,
    because that is what additive blending of R, G and B is.
    """
    full = 255 if dtype == "uint8" else 65535
    return [
        {"selection": {"c": i, "z": 0, "t": 0}, "color": color,
         "contrastLimits": [0, full], "visible": True}
        for i, color in enumerate([[255, 0, 0], [0, 255, 0], [0, 0, 255]])
    ]


def _convert(src: Path, workspace: Path) -> dict:
    """Rewrite ``src`` as a tiled, pyramidal OME-TIFF. Faithful port."""
    import numpy as np
    import tifffile

    # Already OME? Hand it straight to Viv.
    try:
        with tifffile.TiffFile(str(src)) as tf:
            if tf.is_ome:
                photometric = getattr(tf.pages[0], "photometric", None)
                return {"ok": True, "path": str(src), "converted": False,
                        "rgb": str(getattr(photometric, "name", photometric)).upper() == "RGB"}
    except Exception:
        pass  # unreadable by tifffile — the loaders below deal with it

    cache = workspace / ".pantheon" / "atrium-ome"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{src}:{src.stat().st_mtime_ns}:v3".encode()).hexdigest()[:20]
    dst = cache / f"{key}.ome.tif"
    side = cache / f"{key}.json"

    if dst.exists() and side.exists():
        try:
            out = json.loads(side.read_text())
            out.update({"ok": True, "path": str(dst), "cached": True})
            return out
        except Exception:
            pass  # unreadable sidecar — fall through and rewrite

    a = None
    why = []
    try:
        a = tifffile.imread(str(src))
    except Exception as e:
        why.append("tifffile: %s" % e)
    if a is None:
        # tifffile hands compressed codecs to imagecodecs, which is often
        # absent, so ordinary LZW files land here. Pillow decodes those itself.
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            im = Image.open(str(src))
            frames = []
            try:
                while True:
                    frames.append(np.array(im))
                    im.seek(im.tell() + 1)
            except EOFError:
                pass
            # Pages of equal size are channels or a z-stack; pages of
            # different sizes are the file's own resolution pyramid — take the
            # largest and build our own.
            shapes = {f.shape for f in frames}
            if len(shapes) == 1 and len(frames) > 1:
                a = np.stack(frames)
            else:
                a = max(frames, key=lambda f: f.shape[0] * f.shape[1])
            del frames
        except Exception as e:
            why.append("PIL: %s" % e)
    if a is None:
        return {"ok": False, "error": " | ".join(why)[:600]}

    a = np.asarray(a)
    while a.ndim > 3:
        a = a[0]

    # Normalise to channel-first (C, Y, X). "rgb" records that the channels
    # are red, green and blue rather than three stains — OME-XML cannot say so
    # once the samples are separated.
    rgb = False
    if a.ndim == 2:
        a = a[np.newaxis]
    elif a.shape[-1] in (3, 4) and a.shape[0] not in (3, 4):
        a = np.moveaxis(a, -1, 0)
        rgb = True

    # Cap the base resolution: the rewrite is for viewing, and the wait lands
    # in what Viv pulls over the tunnel, not in the rewrite itself.
    step = 1
    while max(a.shape[-2], a.shape[-1]) // step > MAX_BASE_EDGE:
        step *= 2
    if step > 1:
        a = a[..., ::step, ::step]

    a = np.ascontiguousarray(a)

    # uint8/uint16 pass through untouched. Anything else is rescaled — not
    # cast, which would clip floats to zeros — channel by channel, with
    # percentiles from a stride-sampled view.
    if a.dtype not in (np.uint8, np.uint16):
        sample = a[..., ::4, ::4]
        lo, hi = (float(x) for x in np.percentile(sample, [0.1, 99.9]))
        del sample
        if hi <= lo:
            lo, hi = float(a.min()), float(a.max() or 1.0)
        scale = 65535.0 / max(hi - lo, 1e-9)

        out = np.empty(a.shape, dtype=np.uint16)
        for i in range(a.shape[0]):
            plane = np.clip((a[i].astype("float32") - lo) * scale, 0, 65535)
            out[i] = plane.astype(np.uint16)
            del plane
        del a
        gc.collect()
        a = out

    # Halve until the smallest side is under a tile; write levels straight
    # out so they are never all resident at once.
    opts = dict(tile=(512, 512), compression="zlib", photometric="minisblack")
    levels = 0
    cur = a
    while min(cur.shape[-2:]) > 512 and levels < 6:
        cur = cur[..., ::2, ::2]
        levels += 1
    del cur

    with tifffile.TiffWriter(str(dst), ome=True, bigtiff=True) as tw:
        tw.write(a, subifds=levels, metadata={"axes": "CYX"}, **opts)
        level = a
        for _ in range(levels):
            level = np.ascontiguousarray(level[..., ::2, ::2])
            tw.write(level, subfiletype=1, **opts)
        del level

    shape = list(a.shape)
    dtype = str(a.dtype)
    del a
    gc.collect()

    result = {"ok": True, "path": str(dst), "converted": True,
              "shape": shape, "levels": levels, "downsampled": step,
              "rgb": rgb, "dtype": dtype, "bytes": dst.stat().st_size}
    try:
        side.write_text(json.dumps(result))
    except Exception:
        pass  # the rewrite is what matters; a missing sidecar only costs a redo
    return result


def register(ctx):
    @ctx.method
    async def prepare(path: str) -> dict:
        """Everything Viv needs to open ``path``: a servable URL for an OME
        container, and the channel setup when the image is true colour."""
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {path}")

        low = path.lower()
        if low.endswith((".ome.zarr", ".zarr")):
            return {"url": await ctx.serve(src), "type": "ome-zarr", "prepared": True}

        info = _convert(src, ctx.workspace)
        if not info.get("ok"):
            raise RuntimeError(info.get("error") or "conversion failed")

        out = {
            "url": await ctx.serve(info["path"]),
            "type": "ome-tiff",
            "prepared": True,
            "converted": bool(info.get("converted")),
            "bytes": info.get("bytes"),
        }
        if info.get("rgb"):
            out["channels"] = _rgb_channels(str(info.get("dtype", "uint8")))
        ctx.log(f"prepare {src.name}: converted={out['converted']} bytes={out.get('bytes')}")
        return out
