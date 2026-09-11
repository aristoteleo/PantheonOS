# App repositories and installation

- **System** is an essential OS component. It ships with the runtime and does
  not pretend to track a public App's `main` branch.
- An installed App lineage has **one local Git repository**. Store's public
  repository is its `upstream` remote, not another local installation.
- **official** is the local branch tracking the official remote's `main`.
  **my-work** (shown as My fork) is a real development branch in the same repo.
  Creating another development branch preserves repository identity and tags.
- **Versions** opens the combined Git graph directly, with branch heads, tags,
  parent links and uncommitted changes. Selecting a branch here inspects its
  committed files; it does not check out or change the developer's working tree.
- **Pull update** fetches the reviewed remote `main` and advances `official`.
  It does not merge into a development branch or modify its dirty files. To
  incorporate those updates, explicitly merge `official` in Develop/Terminal.
  Later upstream updates must fast-forward. Conflicts remain for resolution.
- The launch default follows the development branch's latest commit unless
  the user explicitly chooses another branch or pins a tag/commit. Launch
  snapshots are immutable; branch selection and updates leave open instances
  unchanged. Uncommitted files appear in the graph but are not release versions.
- **Contribute** publishes a chosen local release to the user's public remote,
  then proposes a pull request to official or another public repository.
  Local and public upstream repository identities are distinct. Maintainers
  review pinned commits; stale reviews cannot merge different code.

The Store serves real read-only Git clone/fetch endpoints. Its durable public
main is the latest published/merged commit. Writes currently enter through
Store's release and reviewed pull-request APIs, rather than raw `git push`.
Local commits stay private until publication; publication exports the chosen
commit and its ancestry, not unrelated private branches.

## Optional applications

`pantheon/apps/official-store.json` records the initial public repositories and
commits extracted from this OS repository. Their code is now maintained in
Store. Cytoscape, Gosling, IGV, ImageJ.js, Mol*, MSA, PhyloTree, QuPath, RDKit,
Spatial 3D, Vitessce, Viv and Volume 3D are installed from Discover. They are
not in a fresh user's App registry, Launcher or backend inventory until installed.

The remaining builtins supply the Desktop, Agent, Store, settings, files,
terminal, Fleet, browser, basic file viewers and existing Agent runtime services.
Jupyter remains a dependency of the default Agent workflows. QuPath's small,
trusted native display/automation driver remains OS integration code; the App
package and installation are independent.

Old installations and their known private forks are consolidated locally.
The existing development checkout keeps its path, commits, tags, index and dirty
files. Old refs and the old checkout are retained for recovery, outside active
App discovery; old repository IDs resolve through aliases and existing snapshots
remain valid. A dirty old installation is not hidden or overwritten automatically.

Pre-Store history can have a different root from today's public repository.
The first explicit pull binds `official` to real public `main` while retaining
its former commit. An explicit merge into development uses the recorded legacy
base to connect both histories and perform a normal three-way merge. It creates
new merge commits, never rebases old commits or moves version tags. Review any
conflicts before committing/publishing. System runtime components still follow
their Desktop/Fleet upgrade path; a branch does not replace compiled runtimes.

To check out optional source for development, explicitly run:

```sh
python scripts/checkout_store_apps.py --store-url https://staging.aristoteleo.com --destination /path/to/app-repositories
```

`export_store_apps.py` is the one-time tracked-source extraction tool. Official
publication uses Hub's `scripts/publish_official_apps.py`, which requires an
administrator and refuses to replace an existing tag or official identity.
