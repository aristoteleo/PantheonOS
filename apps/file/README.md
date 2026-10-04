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

### Prepared native Files package

The opt-in filesystem distribution can be built from the runtime checkout:

```sh
PYTHONPATH="$PWD" python apps/file/build_managed.py \
  --output /absolute/path/to/files-package --platform darwin-arm64
```

Stage this directory through Fleet's ordinary artifact API. Its backend requires
prepared configuration `values.files.workspace` naming an existing absolute path
on the provider node. Optional `values.files.limits` sets positive integer
`max_file_read_chars`, `max_file_read_lines` and `max_glob_results`. The runtime
uses these explicit limits and does not discover Agent/global configuration or
fall back to the Agent's template directories.

This v0.6.10 candidate advertises the existing `fs@1` and `outline@1` contracts plus
basic directory/list/stat operations, reusing the original ToolSet implementation.
It also provides `image-preview@1` through `fetch_image_base64(image_path, max_size)`.
Previews resolve inside the provider's configured workspace, including resolved
symlinks, and never consult an Agent image store. Raster previews reuse the Pillow
encoder with a 10 MiB source byte limit, 40 million source pixel limit and at most
two concurrent encoding workers. The longest output edge is configurable from
1 to 4096 pixels. GIF/SVG bytes are preserved within the byte limit; their display
dimensions are not limited by the raster resize. Original files are unchanged.
The package pins Pillow 12.1.0, already used by the Agent distribution.
It has no Agent, model SDK or shared ToolSet bus. Ordinary dependency grants choose
the allowed methods and arguments. There is no per-Agent resource session: two
consumers can use the same provider with independent grants, and revoking one does
not delete project files or stop the service. `workspace` is a path default, not an
OS sandbox; constrain paths in grants or use an appropriate OS-level boundary.

This package does not yet replace the shipped combined FileManager. Transfer and
document preview helpers, model-assisted inspection/image generation, and optional document
backends remain on that legacy entry until their independent App dependencies are
delivered. The managed manifest does not claim those unavailable methods. Full
Files/UI cutover must preserve these capabilities before replacing the legacy
service.
