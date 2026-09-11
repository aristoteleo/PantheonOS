# Agent View

Display an interactive view authored by an agent inside a Pantheon desktop window.

## Using this App

Use this surface when a task produces an interactive interface rather than a static file. The agent supplies the view through the Desktop/LiveView integration; the window displays that view and its current state.

This package is the desktop host for the view. It does not bundle a fixed visualization, a general code editor, or a standalone web server. If a view cannot load, inspect the endpoint and the service that produced it.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

The frontend is supplied by the desktop UI bundle; this directory declares its App integration.
