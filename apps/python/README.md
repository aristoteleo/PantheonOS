# PythonInterpreter

Execute Python in persistent Jupyter-kernel sessions in the workspace.

## Using this App

Use `manage_interpreters` to create, inspect or manage interpreter sessions, and `run_python_code` to execute code in a selected session. Variables persist in that kernel between calls. Inspect returned output and errors before continuing a dependent step.

Packages and files must be available in the interpreter’s environment. A kernel restart clears variables. To give the user an editable notebook as well as computed results, use the Jupyter App and save the notebook; interpreter execution alone does not create a notebook document.

## Agent interface

Available tools: `manage_interpreters`, `run_python_code`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
