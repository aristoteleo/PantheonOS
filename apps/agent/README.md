# Agent

Talk to Pantheon, follow the team’s work, and return to earlier conversations from a desktop window.

## Using this App

Open **Agent** from the launcher or desktop. Send a message describing the task and attach the relevant files. The conversation shows responses, tool activity and requests for input. Use the timeline and output views to inspect the work and its deliverables.

Opening a chat window does not create a new model or compute node. Execution uses the workspace’s configured Agent service and model settings. Configure those in Settings.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

The frontend is supplied by the desktop UI bundle; this directory declares its App integration.
