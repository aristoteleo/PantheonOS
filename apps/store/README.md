# Store

Discover and install Apps, inspect their documentation, and manage local Git branches and public contributions.

## Using this App

**My apps** lists installed Apps. Open an App’s details to read its README, inspect Skills & API, view Git history, open its source or develop a version. **Discover** lists public repositories and installable releases. Apps moved to **Trash** can be restored; deleting them there removes their retained local files.

An installed public App uses a local repository with an `official` branch tracking public `main` and, after development begins, a `my-work` branch for personal changes. Pulling updates advances `official` without silently overwriting development work. Choose the launch branch or pin a version in the App details; existing windows keep the version they opened.

Publishing shares a committed, versioned release. Contribute creates a pull request to an upstream repository for review; it does not directly overwrite the maintainer’s branch. System Apps ship with the Pantheon runtime and follow runtime upgrades.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

The frontend is supplied by the desktop UI bundle; this directory declares its App integration.
