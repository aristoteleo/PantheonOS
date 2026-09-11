# FileTransfer

Transfer files between a workspace file backend and the frontend using bounded chunks and streaming.

## Using this App

Clients open a file handle for reading or writing, transfer chunks, then close the handle to flush writes and release resources. Positional reads support independent ranges without moving a shared file cursor; streamed reads deliver chunks to the caller’s inbox.

This backend supports user-facing upload and download flows. It has no standalone window. Use the Files app for interactive transfers, and Fleet’s transfer interface when copying between machines. A handle belongs to the backend instance that opened it.

## Agent interface

Available tools: `close_file`, `open_file_for_read`, `open_file_for_write`, `read_chunk`, `read_chunk_at`, `read_file`, `stream_read`, `write_chunk`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
