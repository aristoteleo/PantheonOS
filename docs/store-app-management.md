# App repositories, releases and development

Desktop and Store 0.10.0 use a private development repository and a public
release remote. The public remote is a real Git repository: anyone can clone
or fetch it with Git, and Store users can fork a release into their own space.
Existing Apps are not published automatically.

## Identity and ownership

- `app_id` is the manifest identity, used by launchers, actions and compatibility
  checks. Store groups sources of the same App into one row.
- `repository_id` is a stable UUID identifying one source repository. Two forks
  can share an App id and tag names without sharing code, state or defaults.
- A personal repository lives on the Desktop node, in user Apps or the managed
  private forks directory. Official Apps stay in PantheonOS; independent local
  Git mirrors track their shipped versions without modifying that checkout.
- A fork gets a new repository UUID and keeps Git ancestry. Public forks record
  the upstream repository, release and commit. A fork remains private until its
  owner explicitly publishes it.
- Legacy user copies remain discoverable. Removed personal repositories move to
  recoverable Trash with their entire working tree and Git history. Restoring
  one preserves its identity and does not select it as the default.

## What a release means

A local commit or tag is private. **Publish** creates or updates a public Store
repository with the same repository UUID. A release binds a semantic version,
`v<version>` tag and full commit SHA. The tag cannot be replaced. Retrying the
same version and commit is idempotent; a different commit is a conflict.

The release bundle contains the selected tag and its reachable commit history.
Other local branch and tag refs are excluded. Ancestors of the published commit
are public too, including old file contents; publication is not limited to the
current working tree. Unmerged development branches are not exported. Later
unpublished edits stay local even when a repository has a public remote.

Store exposes `/api/store/repositories/<repository_id>.git` through Git's
[smart HTTP backend](https://git-scm.com/docs/git-http-backend). `clone` and
`fetch` work without authentication for public repositories. The remote's `main`
points at the most recently published release; previous immutable tags remain
available. A subsequent release must descend from at least one existing release,
allowing both forward development and maintenance releases on earlier branches.

Direct Git push is not enabled. Publication goes through the authenticated,
validated Store release API; pushing an arbitrary development branch would
otherwise bypass immutable tags and expose unpublished work. Public source is
therefore available for Git-based collaboration without making Store a full
GitHub-style forge with pull requests or arbitrary branch hosting.

The first public release of a fork records its upstream lineage. Hub verifies
that the chosen upstream release is public, has the same App id, and is an
ancestor of the new release. Fetching upstream in a private fork writes
`refs/remotes/upstream/main` and `refs/remotes/upstream/tags/*`, preserving local
tags, working files and the current branch. Review and merge explicitly.

## Versions and instances

A launch is pinned by **App id + repository UUID + scope + commit**. It reads an
immutable source snapshot, not the editable working tree. Different versions or
forks can run together with separate backend processes and version state.

**Set default** only changes future launches. It does not replace open windows
or restart running backends. **Launch version** opens the chosen snapshot;
headless package backends can be started, listed and stopped by version. Existing
windows with legacy scope/commit revisions continue resolving their snapshots.

This applies to independently packaged DOM frontends and file-based Python
backends. Desktop `ui:` components, node-managed services and native stream
runtimes still require the Desktop/Fleet runtime deployment mechanism. Their Git
history is visible; Store explains the restriction instead of offering a switch
that would not actually change the running code. The Agent tools do not install
new environment dependencies automatically.

## Agent workflow

Use the Desktop tools for development: the Desktop node owns these files and
may be different from the Agent worker's shell node. The management methods are
agent-visible and share the same implementation as the Store UI.

1. `desktop_store_apps()` returns installed repositories, UUIDs, paths and status.
2. Create with `desktop_app_develop(action="create", app_id="my-app")`, or fork
   an existing source with `desktop_store_manage(action="fork", app_id=...,
   scope=..., repository_id=..., name="Experiment")`. Supplying a name makes a
   distinct fork. To fork a public release directly, use
   `desktop_app_store(action="fork", repository_id=..., version="1.0.0")`.
3. Use `desktop_app_develop` actions `files`, `read`, `branch`, `write`, `diff`,
   `test`, `commit`, `switch` and `merge`. All source paths are relative to the
   selected repository. `write` takes a map of filenames to text. `test` takes
   an argv list, such as `["python", "-m", "pytest"]`, executes there and returns
   bounded output with the exit code. Tests time out after 120 seconds.
4. Pass `expected_commit` when editing to detect intervening commits. Commit
   before switching branches. Merge conflicts stay available to inspect, fix
   and commit. Official sources and downloaded public installations must be
   forked before editing.
5. `desktop_store_manage(action="tag", ..., version="1.1.0")` updates the manifest,
   checks compatibility, commits changes and creates a **local** immutable tag.
   `desktop_store_git(...)` shows actual branch/merge history.
6. Resolve a tag with `desktop_store_manage(action="resolve", ..., version="v1.1.0")`.
   Pass the returned `revision` to `desktop_open(app=..., revision=...)` or
   `app_call(app_id=..., method=..., revision=...)`. This allows testing a version
   without changing the default. For a backend use management actions `start`,
   `instances`, and `stop` (the same repository and version).
7. Use management action `default` with the selected commit only when the user
   wants future launches to use it.
8. When the user asks to release publicly, management action `prepare` returns
   the exact tag, commit, manifest and bundle size for review. Then
   `desktop_app_store(action="publish", ..., name="my-store-name",
   expected_commit="<reviewed SHA>", changelog="...")` publishes with the user's
   Store identity. Later releases use the same repository. A successful publish
   records its `store` Git remote and public repository metadata locally.

`desktop_app_store` also provides `search`, `inspect` and `fetch`. Hub-hosted
nodes receive the configured Store URL and a scoped user token. Standalone
nodes can use Store CLI login or the corresponding environment configuration.
No credentials are passed through Agent tool arguments or results.

## Storage and rollout

Postgres is the durable source for validated release metadata and bundles. Hub
materializes bare Git repositories into a content-addressed local cache from
those releases; another replica or a cache miss reconstructs the same refs. No
shared writable Git volume or additional database migration is needed beyond
the existing App-release schema. Private legacy packages remain private; Git
HTTP checks visibility before accessing a cached repository.

Bundle validation does not run hooks, builds or App code. Limits remain 32 MiB
per bundle, 128 MiB and 10,000 files per checkout. Symlinks, submodules and
unsupported manifest protocols are rejected. Git HTTP is limited to clone/fetch
endpoints and bounded requests. The protocol implementation is mirrored between
`pantheon/apps/store_release.py` and Hub's `pantheon_hub/core/app_release.py`.

Deploy the matching Hub, Desktop/Store runtime and UI together. Older nodes
report missing capabilities until upgraded. No existing user's repository is
published, removed or made the default as a side effect of deployment.
