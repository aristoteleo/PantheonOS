# Fleet

Fleet is the desktop's distributed task manager. Open it from the app launcher or with `desktop_open(app="fleet")`.

- **Nodes** lists all nodes registered in your Fleet, their connection status, CPU and memory usage, capacity, and available disk space.
- **App instances** lists supervised services, Desktop windows, and packaged App backends, grouped or filtered by node. Versions are reported by the running instance, not inferred from the installed catalog. Older instances may not report a version.
- **Files** opens the selected machine in the Files app. Only machines with an advertised FileManager backend or workspace filesystem capability offer this action.

The view refreshes every ten seconds while active. Stale node heartbeats are marked offline; their last reported instances remain visible. A failure to read one Desktop's windows does not hide other nodes or services. Fleet only lists nodes belonging to the authenticated user's Fleet.

## Agent interface

`fleet_list_nodes()` and `fleet_list_apps(node_id=...)` inspect the same Fleet. A Fleet window also exposes `refresh`, `show_nodes`, `show_instances(node_id=...)`, and `open_files(node_id=...)` through `desktop_call`.

File locations carry both a node ID and an absolute path. Register an existing deliverable with:

```python
register_output(path="/reports/chart.png", node_id="<node from fleet_list_nodes>", kind="figure")
```

Omit `node_id` for the current workspace. Registration checks the owning FileManager and stores the source node. An unavailable node is an error, never a reason to recreate a file on the Agent node. Same-name files on different nodes remain distinct outputs.

The Files app browses, previews, edits, uploads, downloads, renames and deletes through the selected node's file backend. Paths are preserved when opening a viewer. Desktop-owned files retain HTTP range and directory serving. Other machines use bounded RPC reads for portable previews (up to 64 MiB); downloads can stream to disk in browsers supporting a save-file picker. Directory-backed viewers need an HTTP data endpoint. Cross-machine moves use Fleet's existing `transfer` API, rather than an ordinary filesystem rename.

## Personal node file sharing

Fleet 0.3.1-alpha shares the current user’s home directory in Files on first startup, with no extra parameters or Python installation. **Add node** uses this default and offers **Turn off file sharing** under File access options. Existing saved folder restrictions or opt-outs are preserved on reconnect and upgrade.

Use `fleet up --no-files` to disable Files access and remember that choice, or `fleet up --share-dir /absolute/path` to share only specific folders (repeatable). To re-enable home access, use `fleet up --share-dir '~'`. These choices affect the Files backend; shell tasks remain available. In **Shared folders**, leave the folder list empty to generate a command that restores home sharing. Run the command on that machine; existing credentials and node identity are reused.

The **Add node** button and card belong only to Nodes. Switching to App instances closes the setup cards.
