"""ImageJ.js file preparation, isolated from the shared Python kernel."""

import gc, hashlib, json
from pathlib import Path

# One function, because an early exit must be a plain return: SystemExit raised in
# here escapes into the agent's event loop and kills the pod. See the note in
# network/bioimage.ts.
def _prepare(SRC, WORKSPACE):
    src = Path(SRC)
    if not src.exists():
        return {"ok": False, "error": "file not found"}

    import numpy as np
    import tifffile
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None

    # What ImageJ1's own reader copes with.
    OK_COMPRESSION = {"NONE", "LZW", "PACKBITS", "DEFLATE", "ADOBE_DEFLATE"}
    # Above this it is not a question of format any more; ImageJ holds the
    # whole image on a 32-bit JVM.
    MAX_PIXELS = 60_000_000

    info = {}
    needs = None
    try:
        with tifffile.TiffFile(str(src)) as tf:
            page = tf.pages[0]
            comp = str(getattr(page.compression, "name", page.compression))
            series = tf.series[0]
            pixels = 1
            for d in series.shape:
                pixels *= int(d)
            info = {
                "compression": comp,
                "bigtiff": bool(tf.is_bigtiff),
                "shape": [int(d) for d in series.shape],
                "dtype": str(series.dtype),
                "pages": len(tf.pages),
                "is_ome": bool(tf.is_ome),
            }
            if tf.is_bigtiff:
                needs = "BigTIFF"
            elif comp.upper() not in OK_COMPRESSION:
                needs = comp.replace("_", " ").title() + " compression"
            elif pixels > MAX_PIXELS:
                needs = "%.0f megapixels" % (pixels / 1e6)
            elif str(series.dtype) not in ("uint8", "uint16", "float32"):
                needs = str(series.dtype)
    except Exception as e:
        info = {"error": str(e)}
        needs = "a layout tifffile could not describe"

    if needs is None:
        return {"ok": True, "path": str(src), "converted": False, "info": info}

    cache = Path(WORKSPACE) / ".pantheon" / "atrium-imagej"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{src}:{src.stat().st_mtime_ns}:v1".encode()).hexdigest()[:20]
    dst = cache / f"{key}.tif"
    if dst.exists():
        return {"ok": True, "path": str(dst), "converted": True,
                "reason": needs, "info": info}

    a = None
    why = []
    try:
        a = tifffile.imread(str(src))
    except Exception as e:
        why.append("tifffile: %s" % e)
    if a is None:
        try:
            im = Image.open(str(src))
            im.seek(0)
            a = np.array(im)
        except Exception as e:
            why.append("PIL: %s" % e)
    if a is None:
        return {"ok": False, "error": " | ".join(why)[:600], "info": info}

    a = np.asarray(a)
    while a.ndim > 3:
        a = a[0]
    # Channel-last is what ImageJ expects for a colour image; a stack of planes
    # it reads as a stack.
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[-1] not in (3, 4):
        a = np.moveaxis(a, 0, -1)

    # Decimate until it is a size ImageJ can hold. Plain striding, so no
    # interpolation buffers.
    step = 1
    while (a.shape[0] // step) * (a.shape[1] // step) > MAX_PIXELS:
        step *= 2
    if step > 1:
        a = a[::step, ::step]
    a = np.ascontiguousarray(a)

    if a.dtype not in (np.uint8, np.uint16):
        sample = a[::4, ::4]
        lo, hi = (float(x) for x in np.percentile(sample, [0.1, 99.9]))
        del sample
        if hi <= lo:
            lo, hi = float(a.min()), float(a.max() or 1.0)
        a = np.clip((a.astype("float32") - lo) * (65535.0 / max(hi - lo, 1e-9)),
                    0, 65535).astype(np.uint16)

    # Uncompressed, classic TIFF: the one thing ImageJ1 always reads.
    tifffile.imwrite(str(dst), a, bigtiff=False, compression=None)
    shape = [int(d) for d in a.shape]
    del a
    gc.collect()

    return {"ok": True, "path": str(dst), "converted": True,
            "reason": needs, "downsampled": step, "shape": shape,
            "info": info}


def register(ctx):
    @ctx.method
    async def prepare(path: str) -> dict:
        src = Path(path).expanduser()
        if not src.is_absolute():
            src = ctx.workspace / src
        src = src.resolve()
        if not src.is_relative_to(ctx.workspace.resolve()):
            raise ValueError("ImageJ.js files must be inside the workspace")
        if not src.exists():
            raise FileNotFoundError(f"File not found: {path}")
        info = (_prepare(str(src), str(ctx.workspace)) if src.suffix.lower() in (".tif", ".tiff")
                else {"ok": True, "path": str(src), "converted": False})
        if not info.get("ok"):
            raise ValueError(info.get("error") or "Could not prepare the image")
        return {**info, "url": await ctx.serve(info["path"])}
