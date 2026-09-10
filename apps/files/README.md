# Files

Browse files in the workspace and on machines registered in your Fleet. The Locations sidebar lists every Fleet node with a workspace filesystem or advertised FileManager backend. Select a node to browse its root directory, or choose Workspace to return to your current project.

File operations retain the selected node: opening, saving, upload, download, rename and deletion all reach that node's backend. An offline node shows its status and cannot silently redirect operations to the workspace. Use **Fleet** in the sidebar to view node resources and App instances.

Agents can navigate an existing Files window with `desktop_call(window_id=..., action="navigate", kwargs={"path":"/reports", "node_id":"<fleet-node-id>"})`. Node IDs come from `fleet_list_nodes`; the path is absolute on that node. A file's node-qualified URI is an internal location identifier, not a public URL.
