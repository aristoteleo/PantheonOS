# Shell

Execute shell commands in the workspace using Fleet Runner’s native Go command backend.

## Using this App

Use `run_command` for a command and inspect its exit status and output. The `shell` interface also supports named shell sessions through `new_shell`, `run_command_in_shell`, `get_shell_output` and `close_shell`.

Commands run on the selected execution backend with its working directory, environment and installed programs. Use Terminal when the user should interact with a visible PTY, and Fleet to address another node. This system package describes the Go implementation compiled into Fleet Runner.

## Agent interface

Available tools: `run_command`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.
