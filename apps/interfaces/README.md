# Interfaces

Browse interactive interfaces exposed by the agent’s services.

## Using this App

Open **Interfaces** from the desktop launcher to find the interfaces available to the current workspace. Select a served interface to use its controls and inspect its content.

Interfaces is the frontend entry point. The service that registered an interface owns its data and behavior; a stopped or unavailable endpoint cannot be repaired by reopening this window. Use the Agent conversation to return to the task that created it.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

The frontend is supplied by the desktop UI bundle; this directory declares its App integration.
