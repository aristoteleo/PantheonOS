# Node Files

A Go file backend compiled into Fleet Runner. It provides Files with browsing, text editing, directory creation, rename/delete and bounded binary transfers on a personal machine. No Python installation is needed.

## Enable sharing

In **Fleet → Nodes → Set up files**, enter folders on the target machine and run the generated command there. Alternatively, after installing Fleet 0.3.0-alpha or later:

```sh
fleet up --share-dir /absolute/path/to/Shared --share-dir /another/folder
```

`--share-dir` is repeatable. Supplying it replaces the saved shared-folder list. Ordinary `fleet up` reuses it; `fleet up --no-files` clears it. Configuration lives in the node's private state directory (`file-shares.json`). It cannot be set or widened by an App start request. No folders are shared by default.

On macOS, allow the OS prompt for a selected protected folder when needed. The installer primes only selected folders. On Windows the PowerShell installer accepts `-ShareDir @('C:\Users\you\Shared', 'D:\Data')` and `-NoFiles`.

## Runtime and API

The node advertises `fs:local` and `file_roots` when configured. This is separate from `fs:workspace`, which identifies the cloud workspace. The Python resolver starts `node-files` as a **builtin** instance on the selected node on first access. Fleet reports the instance and its health normally.

The service implements the FileManager protocol: `get_cwd`, `list_files`, `stat_path`, `read_file`, `write_file`, `create_directory`, `delete_path`, `move_file`, `manage_path`, and `file_transfer`. Transfer methods are `open_file_for_read`, `open_file_for_write`, `read_chunk_at`, `read_chunk`, `write_chunk`, and `close_file`. Chunks are bounded to 256 KiB. File handles are scoped to the instance, limited in number, and expire after inactivity.

`/` is a virtual list of shared folders; entries include their absolute `path`. All operations use the owning node and its absolute file paths. Go's `os.Root` enforces the directory boundary during filesystem operations, including symlink traversal. Shared roots cannot be deleted or renamed through the API. Renaming across different shared roots is rejected; use a copy/transfer instead. Text reads are bounded previews; use transfers for large or binary files.

Workspace FileManager remains available for Python-specific code analysis and document/image processing. These advanced tools are not dependencies of the native Files backend.
