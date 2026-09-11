# Jupyter

Edit Jupyter notebooks and execute cells with output streaming in a desktop window.

## Using this App

Open a `.ipynb` file from Files with **Jupyter**, or launch Jupyter to work with notebooks in the workspace. Edit code and Markdown cells, run code in a kernel, and inspect the resulting text, tables and figures.

Agents can read and edit a notebook through `notebook_read` and `notebook_edit`, or control the notebook already open in a window with its cell actions. Use returned cell IDs when updating or executing cells. `notebook_execute` manages execution and kernel lifecycle; execution needs a kernel with the required packages installed.

Notebook files and a kernel’s in-memory variables are separate. Save the notebook for persistent edits and outputs; restarting a kernel discards its in-memory state.

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
