# Jupyter

Edit Jupyter notebooks and execute cells with output streaming in a desktop window.

## Using this App

Open a `.ipynb` file from Files with **Jupyter**, or launch Jupyter to work with notebooks in the workspace. Edit code and Markdown cells, run code in a kernel, and inspect the resulting text, tables and figures.

## Choose an execution node

Right-click Jupyter and choose **Open on node…**, or choose a node in the app launcher's details panel. **Fleet → App instances → Default backends → Jupyter** sets the default for new windows. The window's backend badge identifies its bound node. An explicit node choice never silently executes on another machine.

The selected node runs the notebook engine and its Python kernels; Python environments listed in Jupyter belong to that node. Linux, macOS and Windows nodes need Python 3.10+ and Fleet's managed App/RPC support. First launch installs and caches Jupyter dependencies in a private environment; an Agent server or Docker is not required on that node.

On the existing Workspace node, the notebook directory is preserved. Other nodes have their own durable App notebook directory. Files and running variables are not transferred when selecting another node. Stop the App in Fleet to shut down its kernels; saved notebooks remain. Existing windows keep their node binding.

Agents controlling a node-bound notebook should use its window actions or `app_call(app_id="integrated-notebook", node_id=..., method=..., args=...)`. The legacy chat `notebook_*` tools still address the chat's Workspace notebook service.

Agents can read and edit a notebook through `notebook_read` and `notebook_edit`, or control the notebook already open in a window with its cell actions. Use returned cell IDs when updating or executing cells. `notebook_execute` manages execution and kernel lifecycle; execution needs a kernel with the required packages installed.

Notebook files and a kernel’s in-memory variables are separate. Save the notebook for persistent edits and outputs; restarting a kernel discards its in-memory state.

## Interactive widgets

Jupyter renders `ipywidgets` 8 controls (including layouts, buttons, sliders,
images and Output) and `ipycanvas` 0.14 in the notebook. Events and binary drawing
data travel over the same authenticated App connection as cell execution, on the
selected Fleet node. No additional public Jupyter server or browser extension is
needed. Updates continue after the cell finishes executing.

The managed App environment includes these Python packages. If you select your
own Python environment, install them in that kernel with
`%pip install "ipywidgets>=8.1,<9" "ipycanvas>=0.14,<0.15"` and rerun the widget cell.
Reopening a notebook reconnects to its live models; a restarted or stopped kernel
requires rerunning the cell. Canvas apps should use `canvas.on_client_ready(draw)`
to redraw after a long disconnection (or `sync_image_data=True` for a saved image).
Widget events are buffered within a bounded replay window, not stored as an
unlimited drawing history. Saved notebook output alone is not a running widget.

The frontend bundles supported modules locally; it does not fetch arbitrary
widget JavaScript from a CDN. Third-party widget extensions other than ipycanvas
need a frontend integration and display an explicit unsupported-module error.

## Window actions

Call these through `desktop_call` on this App’s existing window. The runtime’s action schema supplies parameters.

- `read_cells`: Read cells and outputs from the notebook open in this window.
- `add_cell`: Add a cell using content (required string; empty only when intentional). Set execute=true to run code in the same call. Returns cell_id and confirms the visible content.
- `update_cell`: Update cell_id with content (required string). Set execute=true to run after updating. Confirms the visible content.
- `execute_cell`: Execute a cell in this notebook and refresh its visible output.

## Agent interface

Available tools: `notebook_edit`, `notebook_execute`, `notebook_read`, `read_notebook_output`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Frontend entry: [frontend/control.js](frontend/control.js).

Backend implementation: [__init__.py](__init__.py).
