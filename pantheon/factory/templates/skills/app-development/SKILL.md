---
id: app-development
name: Developing PantheonOS Apps
description: |
  Create, edit, test, version and publish the user's Apps with
  pantheonos_develop_app; install and configure them with
  pantheonos_manage_app; try a version with pantheonos_start_app.
tags: [apps, development, store, git, publish]
---

# Developing PantheonOS Apps

An App is a package with a manifest (`app.json`), an optional window
(frontend) and an optional backend. Its source is a Git repository on the
node that holds the user's library, which may not be the node your shell
runs on: never assume an App's path exists in your shell. Work on it with
`pantheonos_develop_app`, which edits, commits and tests there.

## Create an App

```python
pantheonos_develop_app(action="create", app="my-app", args={"message": "My App"})
# A private Git repository with app.json and index.js.
pantheonos_develop_app(action="write", app="my-app", args={"files": {
    "app.json": "...", "frontend/index.js": "...", "backend/__init__.py": "..."}})
pantheonos_develop_app(action="commit", app="my-app", args={"message": "First version"})
pantheonos_start_app(app="my_app", window=True)   # open it
```

Layout:

```
app.json             # manifest (below)
frontend/index.js    # export function setup(app, root)
backend/__init__.py  # optional: def register(ctx): @ctx.method async def ...
skill/SKILL.md       # optional: how an agent drives it later
```

A minimal `app.json` (never write the older `atrium.json`):

```json
{
  "id": "my-app",
  "name": "My App",
  "version": "0.1.0",
  "apiVersion": 2,
  "surface": "dom",
  "entry": { "frontend": "frontend/index.js" },
  "launcher": true,
  "icon": { "path": "assets/icon.svg", "tint": "#5b6ee0" },
  "opens": [".myext"],
  "actions": [{ "name": "bump", "description": "increment", "params": {} }]
}
```

The frontend reaches a backend method with `app.call("methodName", args)`.
To call a method yourself, declare it in the manifest's `provides.tools`
(name, description, params). It is then listed as a tool of `my_app`, with
the window actions, and `pantheonos_call_app(app="my_app", tool="methodName",
args={...})` calls it. Only declared tools, actions and parameters can be
called.

A one-off window needs no App: `desktop_open(module=...)` (see the desktop
skill). Make an App when the user will reuse it or it needs a backend.

## Change an existing App

1. `pantheonos_develop_app(action="status", app=...)`: its repository,
   branch and changes. With several copies (an installed release, a fork),
   pass `args.repository` from `pantheonos_manage_app(action="versions")` or
   `status`.
2. An App installed from the Store is not edited in place. `fork` makes a
   private development branch first: `args: {version, name}`.
3. Read before you write. `files`, `read {path}` (with `branch` to read a
   branch without switching), then `write {files: {path: content}}` with the
   last `expected_commit`, which detects a concurrent commit.
4. `diff`, then `commit {message}`. `test {command: ["python", "-m",
   "pytest"]}` runs in the repository (120 s limit).
5. Branches: `branch {branch}` creates and switches, `switch`, and `merge
   {branch}`. A merge conflict stays in the working tree: read the files,
   fix them, commit.

## Versions

* `tag {version: "1.1.0"}` commits and tags a local version after a
  compatibility check. `history` shows branches, tags and merge parents.
* Try a version without changing what launches by default:
  `pantheonos_start_app(app, version="v1.1.0")`, or with `window=True` to
  open it. Your later calls in this conversation go to that instance.
* `pantheonos_manage_app(action="set_version", app, args={"version": ...})`
  changes what future launches use: `branch:my-work` follows a branch,
  `branch:official` follows upstream, a tag or full SHA pins a version,
  `latest` follows the working checkout's HEAD. Running instances are not
  changed. Uncommitted edits never reach a launch.
* `build {node}` builds a version that declares a build for that node's
  platform; `build_status {node}` reports it.

## Store and publication

* `pantheonos_manage_app(action="search", args={"query": ...})` and
  `inspect {repository}` find public Apps; `install {repository, version}`
  installs a release. Professional Apps (Cytoscape, ImageJ.js, IGV, QuPath)
  are Store repositories, not part of the system.
* `fetch` updates a fork's upstream refs without merging; `pull
  {expected_commit}` advances its `official` branch. To take upstream
  changes, commit your work, then `merge {branch: "official"}`.
* A commit or tag is private. Publish only when the user asks:
  `prepare` returns the exact tag and commit to review, then `publish {name,
  expected_commit}` publishes it to a public Store repository, with its
  reachable history. Later releases reuse the repository. Never move a
  published tag.

### Contributions

`community` lists an App's public repositories, including its Official
upstream. To contribute when asked: publish a release of your fork, inspect
both repositories, then `submit {repository, version, target_repository,
expected_commit, expected_base, title, description}`. `contributions
{inbox: "mine" | "review" | "all"}` and `inspect_contribution {request_id}`
show requests and their diffs.

Maintainers: `prepare_merge {request_id, version}`, inspect the candidate,
`checkout_review {request_id, expected_commit}` for a private checkout to
read and test, then `review {request_id, decision: "approve" | "reject",
expected_commit, description}`. Only with the authority to release upstream,
`merge_contribution {request_id, expected_commit}` publishes the merge.
`close_contribution {request_id}` withdraws a request.
