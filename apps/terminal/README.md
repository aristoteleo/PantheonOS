# Terminal

An interactive terminal for the computers in your Fleet. The computer picker
shows the selected machine and connection state. Selecting an online macOS or
Linux node starts its Go PTY backend on demand; input is enabled after the shell
and output stream connect. A node needs the `proc` capability, not a Files backend
or a Python installation. Windows requires a future ConPTY implementation.

Each computer keeps its own shell session. Switching computers detaches the
view without stopping the previous shell; switching back replays its history.
Closing the Terminal window closes its sessions. Browser/Agent reconnects try
to reattach to surviving sessions. If a node or its PTY is unavailable, a
remote command is never redirected to the workspace.

Files → **Open in Terminal** uses the selected computer and directory. PTY runs
as the Fleet Runner's OS user; Files shared-folder restrictions do not sandbox
the shell.

## Agent interface

- `read` reports the current computer, node ID, connection state and visible
  terminal output, along with the available computer identities.
- `select_node(node_id)` selects a Fleet node and waits for PTY readiness.
  An empty node ID selects the workspace.
- `run(command)`, `input(data)`, `interrupt()` and `clear()` use the same shell
  session the person sees. They fail if that session is not connected.

Use the returned node ID rather than guessing a hostname. Select the computer
before sending commands; commands and their output remain visible in Terminal.
