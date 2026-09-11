# Pty

Provide interactive shell sessions for Terminal, including input, output streaming, resize and reconnect.

## Using this App

Terminal selects a machine, starts or attaches to a PTY session, and subscribes to that session’s output stream. The backend exposes `pty_open`, `pty_attach`, `pty_write`, `pty_resize`, `pty_list` and `pty_close` through the `pty` interface.

Fleet Runner includes a Go PTY implementation for macOS and Linux nodes with process execution capability. It does not require Python or a Files backend. Windows PTY support requires a ConPTY implementation. The shell runs as the Runner’s operating-system user; file-sharing roots do not restrict shell commands.

Reconnect to a surviving session by its returned ID rather than creating a replacement shell. Closing a session ends its shell; resizing tells terminal applications their actual row and column dimensions.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.
