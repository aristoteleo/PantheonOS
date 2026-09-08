---
name: qupath
description: Operate the QuPath application on the Atrium desktop to inspect pathology images, edit annotations, run Groovy analysis and export measurements from the current GUI session.
---

# QuPath on the desktop

Use the shared `desktop_*` tools. QuPath supplies app actions through
`desktop_call`; it does not require a separate set of QuPath tools. The
bundled application is QuPath 0.7.0.

## Reach the right session

Find an existing QuPath window with `desktop_windows()`, especially when the
user refers to a window or an open image. Use its stable `window_id` for all
calls. To open a new image or project:

```python
desktop_open(app="qupath", path="/workspace/slide.svs")
# Or: desktop_open(app="qupath", path="/workspace/project/project.qpproj")
```

Specify `app="qupath"` for images because other viewers also handle TIFF and
JPEG. Open standalone `.qpdata` through its owning project. Do not reopen an
image from disk to analyze an existing window: the GUI may contain unsaved
annotations. Changing the launch `path` with `desktop_update` does not change
QuPath's current image. Use the native File menu to change it in place,
including its Save/Cancel interaction.

## Shared desktop controls

| Call | Use |
| --- | --- |
| `desktop_read(window_id)` | Read the current QuPath session and image state. |
| `desktop_screenshot(window_id)` | Inspect the actual native window render. |
| `desktop_act(window_id, actions=[...])` | Use native mouse/keyboard controls, including menus and dialogs. Follow the tool's action schema and screenshot coordinate dimensions. |
| `desktop_call(window_id, action="focus")` | Focus the window. |
| `desktop_call(window_id, action="status", args={"annotation_limit": 200})` | Refresh the GUI state; annotation limit is 0–2000. |
| `desktop_call(window_id, action="close")` | Request normal close, keeping unsaved-work dialogs usable. |

Read state before analysis to confirm the current image, project, image type,
selection and object counts. Query pixel calibration by script when needed. The launch `path` alone is
not proof of the current image. A screenshot is a rendered view, not the
original image pixels or a source of quantitative measurements. `native_windows`
in the read/screenshot result lists current owned dialogs; use the returned
child `window_id` to screenshot or act on a dialog instead of guessing X11 IDs.

`desktop_act` examples, using coordinates from that window's screenshot:

```python
desktop_act(window_id, actions=[{"type": "rightclick", "x": 300, "y": 240}])
desktop_act(window_id, actions=[{"type": "key", "key": "Ctrl+s"}])
```

The tool also supports `click`, `dblclick`, `move`, `drag`, `wheel`, and `text`.
Mouse coordinates are native window pixels, including QuPath menus/toolbars;
ROI coordinates in scripts use full-resolution image pixels.
JavaFX's native key path supports BMP text (including Chinese); non-BMP text
such as many emoji is rejected before sending any action in the batch. For
that text, explicitly use the [focused field script](references/scripting.md#enter-text-in-a-verified-focused-field)
after verifying the intended field/window. This uses the existing GUI and
does not replace the user's clipboard.

## Run scripts in the current GUI

After `desktop_read` completes with bridge state `succeeded`, take
`image_token` from its inner result (`reply["result"]["result"]["image_token"]`).
Pass it as `expected_image` for every mutation, including annotation edits,
analysis, exports and saves. It identifies the current in-memory image,
including unsaved work, rather than only its filename.

```python
import uuid
request_id = uuid.uuid4().hex
desktop_call(window_id, action="run_script", args={
    "script": "return [annotations: getAnnotationObjects().size()]",
    "thread": "worker",
    "args": [],
    "request_id": request_id,
    "expected_image": image_token,
    "update_hierarchy": False,
    "wait_s": 2
})
```

The bridge uses QuPath's Groovy engine with `QPEx` default imports and the
current GUI project/image context. `getCurrentImageData()` reaches that
image's in-memory data, including unsaved edits. Supply script parameters as
strings in `args`; inside Groovy they are available as `args[0]`, etc.
Return a small JSON-compatible map/list/value. Do not return `ImageData`,
`PathObject`, image server or JavaFX objects; extract their IDs and properties.
`println` output is captured with a size limit.

Set `update_hierarchy=False` for read-only, export and save scripts. The default
`True` refreshes the hierarchy after a script and marks the image as changed;
use it for annotation/analysis mutations. `False` only skips that automatic
notification and never clears existing unsaved changes. Save image data with
`qupath.lib.io.PathIO.writeImageData(new File(args[0]), getCurrentImageData())`,
then confirm `desktop_read` reports `image.changed=False` before closing.

Use `thread="worker"` for analysis and exports. Use `thread="fx"` only for
short JavaFX UI/view changes; expensive work there freezes the application.
The bridge checks `expected_image` immediately before a script starts and
fails without running it if the image changed while queued. Explicit `null`
requires no open image; omitting the field leaves the request unguarded.
An image-token mismatch calls for another read and a fresh decision about the
target, not an automatic retry with the new token. Data context stays bound to
the checked image once execution begins. Keep the intended viewer selected
for scripts that use live GUI/viewer methods.

The generic tool's `result` is a bridge envelope with `request_id`, `state`,
`stdout`, `stderr`, and the script's JSON return value in its own `result`.
GUI state uses the same envelope; its value contains `image_token`, `image`, `project`,
`viewer`, and `objects` (counts, selected IDs and a bounded annotation list).
Check the envelope state: `queued`/`running` means pending, `succeeded` means
finished, and `failed`/`expired` requires inspecting the error.

Choose a unique `request_id` for each new operation; keeping it lets you
recover when a tool response is lost. `wait_s` is 0–10 seconds (default 2).
A wait timeout does **not** cancel or roll back execution. Continue with
`desktop_call(window_id, action="script_status", args={"request_id": id})`.
An id is scoped to this GUI session; reusing it with different parameters
fails. An `unknown` status after a restart does not prove an old operation
never ran. If a call's outcome is uncertain, inspect existing request status
and the current image before submitting any mutation again. A failed script may
already have changed some objects or written files.

For inspection, ROI creation and export examples, read
[Live scripting examples](references/scripting.md). For segmentation or
classification, use the installed plugin/model and the workflow parameters
appropriate to the image. A generic cell-detection threshold is not a
validated analysis for every stain or resolution.

After a mutation, read the current state, check the affected objects or output
files, and take a screenshot when appearance matters. Save image/project data
when the task requires it; exporting GeoJSON or measurements does not save
QuPath's entire working state. Closing requests the application's normal save
confirmation rather than discarding work.
