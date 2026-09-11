# Task

Organize agent work into task phases and expose its progress, review requests and output artifacts.

## Using this App

`task_boundary` updates the task’s phase and progress. `notify_user` presents a message or a request for user input. `register_output` adds an existing file or directory to the conversation’s Output panel; `list_outputs` reads registered deliverables.

For a deliverable on another Fleet machine, registration includes its `node_id` and absolute path. The owning file backend validates the file and remains its source for browsing and download. Registration does not copy a missing file between machines or create one.

This package integrates with the agent pipeline and has no standalone window. Its workflow modes such as PLANNING and VERIFICATION are unrelated to the Modal cloud-compute service.

## Agent interface

Available tools: `list_outputs`, `notify_user`, `register_output`, `task_boundary`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
