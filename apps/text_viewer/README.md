# Text Viewer

Read Markdown in a formatted preview and edit text or source code with syntax highlighting.

## Using this App

Open a supported text file from Files with **Text Viewer**. Markdown opens in Preview; switch to the editor to change its source. Save writes the same document through the file bridge, retaining the source machine for remotely opened files.

Agents can call `getText` to inspect the document including unsaved edits. `replaceText` changes editor content without saving and can check `expectedText` to detect intervening changes. Call `save` separately and check its result.

The package includes the built frontend. Source is in `frontend-src/`; use the package’s `build.sh` to rebuild bundled assets after frontend changes.

## Window actions

Call these through `desktop_call` on this App’s existing window. The runtime’s action schema supplies parameters.

- `getText`: Read the text currently visible/editable in this window, including unsaved edits. Supports offset and limit (1–100000).
- `replaceText`: Replace the current document in this window without saving. Optional expectedText prevents overwriting intervening edits.
- `save`: Save the current editor contents to its workspace file and report actual write failures.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Frontend entry: [frontend/main.js](frontend/main.js).
