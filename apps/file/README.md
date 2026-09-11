# FileManager

Read, search and edit workspace files, with code outlines and document/image inspection for agents.

## Using this App

Use `glob` to locate files, `grep` to search contents, and `read_file` or `view_file_outline` to inspect a result. Use `write_file`, `update_file` or `apply_patch` for changes. Document and image tools provide richer inspection when plain text is insufficient.

This is the workspace’s Python FileManager service. **Files** is the graphical file browser; **Node Files** is the native Go backend for folders shared by a personal Fleet machine. Python-specific PDF, image and code-analysis helpers belong to this workspace service, not to every remote node.

## Agent interface

For screenshots, pass the `image_ref` returned by `desktop_screenshot` or
`browser_screenshot` directly to `observe_images`. Plain paths refer to this
file service's machine. An explicit `node_id` or a `pantheon-node:///` image
reference reads from the named Fleet node through its file backend, including
Go Node Files. The node's shared-folder restrictions remain in effect; missing
or offline remote files never fall back to a same-named local file. Transfers
are bounded (8 images, 20 MiB each), and temporary copies are cleaned up.

Available tools: `apply_patch`, `generate_image`, `glob`, `grep`, `observe_images`, `read_file`, `read_pdf`, `update_file`, `view_file_outline`, `write_file`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
