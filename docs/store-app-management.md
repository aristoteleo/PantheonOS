# User App management in Store

Store manages App definitions and releases in the user's space. PantheonOS
continues to ship official Apps; creating a user copy makes an independent Git
repository without changing the official source. An official update advances
that repository's history. It refuses to replace dirty files, local commits,
unmerged work, or a tag whose official files changed without a version bump.

## Inventory and management

`desktop_store_apps` lists workspace, user and official copies, including
headless services and copies shadowed by another scope. It reads manifests on
the Desktop node, not the ChatRoom's filesystem. Icon paths are resolved inside
each App directory and served alongside the inventory; missing icons do not
hide Apps. The UI also includes Apps compiled into the Desktop shell.

Store offers All Apps / Headed / Headless filters. Both kinds support a user
copy, inspection, Git history, release preparation, publication, installation
of a specific version and removal of the user copy. Headless Apps do not get
a window-launch button. Removing a user copy retains external App data.

`backend_state` describes only processes owned by this Desktop supervisor.
It is not cluster-wide state. Bus services (`module:Class` entry points) keep
their existing node lifecycle and official service bindings; a user repository
does not silently replace a running service or its credentials. Likewise,
`ui:` entry points refer to components compiled into the Desktop shell.
Fleet is responsible for cluster-wide instance management.

Mutations share the supervisor's per-App spawn lock and refuse to replace a
running backend. The UI also refuses changes while App windows remain open.
Dependency ranges are checked before install; missing or incompatible
dependencies must be installed separately. Installation stages the complete
tree before replacing the current directory and restores it on failure.

## Git graph and publication

`desktop_store_git` returns actual topological commits, all parents, local and
remote branch refs, tags, and HEAD. It includes detached HEAD and paginates
from 100 up to 1,000 commits. The graph preserves fork and merge edges. An
official App inside the PantheonOS monorepo is not presented as an invented
independent history: first create its user repository.

Select a user App, expand **Git & publishing**, review the changed files, enter
a semantic version, then **Commit changes & tag**. This stages all changed and
untracked App files and enforces the existing interface compatibility gate.
**Prepare release** snapshots a clean checkout at `v<version>`. Review the
tag, fixed commit, package size and visibility before pressing **Publish**.
Publication itself happens through authenticated Pantheon Store HTTP APIs.

The `git-bundle-v1` payload includes a Git bundle, tag, fixed full commit SHA,
and SHA-256 checksum. Binary assets and ancestor history are included, along
with reachable semantic-version tags. Unmerged development branches are not
uploaded. Local Git graphs can show those branches; Store release graphs show
only the published history. Hub derives a bounded graph and portable icon
from the validated bundle, so browsing a release does not require installing
it or downloading the full bundle. Icon tint and monochrome metadata survive
publication; missing and failed images use a placeholder.

Published package/version pairs are immutable. Installing an earlier version
selects that version's exact commit. Git hooks, global/system Git configuration,
builds and App code are not invoked by Hub validation. Checkout rejects
symlinks/submodules, unsupported API versions and missing frontend entries.
Limits are 32 MiB per bundle, 128 MiB and 10,000 files in its checked-out tree;
portable catalog icons are capped at 256 KiB. The protocol module
`pantheon/apps/store_release.py` is mirrored verbatim in Hub's
`pantheon_hub/core/app_release.py` and must be updated together.

## Paired rollout

This requires Desktop 0.7.0, the matching Store UI, and the Hub App-release
migration and API. Apply Hub's `migrations/add_store_app_releases.sql` before
starting the new Hub code; its runtime image must include Git. The bundle is
stored separately as a deferred field so catalog queries do not load it.
Private package metadata, versions and downloads require the owner account.

Use staging and explicitly pinned runtime/UI commits for verification. Update
the running Desktop service only at an agreed interruption point: replacing
its process may interrupt hosted App backends. An old Desktop reports an
unsupported-management error instead of pretending it has no installed Apps.
No user's App is automatically published or migrated by this rollout.
