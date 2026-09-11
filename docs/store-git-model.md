# App repositories and installation

- **System** is an essential OS component. It ships with the runtime and does
  not pretend to track a public App's `main` branch.
- **Official** is an installed public repository designated by Store's
  administrator. Its local `main` tracks `origin/main`.
- **My fork** is an independent private repository. Its working branch tracks
  `upstream/main` from the original repository. It is not a branch inside the
  official repository. Publishing creates its public Store remote.
- **Contribute** opens a pull request against the selected official or community
  upstream. Only that repository's maintainers can review and merge it. Store
  pins the reviewed commits; stale reviews cannot merge a different commit.
- **Pull update** fetches and merges remote `main` after the user asks. Official
  installations require a fast-forward. Forks merge normally. Dirty files are
  refused; conflicting merges abort and report the affected paths. `Fetch`
  alone only updates remote refs. No update silently checks out user files.
- Tags are immutable fixed versions. Running instances use commit snapshots;
  an explicitly pinned default stays pinned after pulling. Otherwise a personal
  fork's latest committed code takes priority, followed by an installed public
  repository's current main.

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

Old users' initialized App repositories are migrated into their installed user
space once, with their Git history, dirty files and explicit defaults intact.
Removing a migrated App does not reinstall it on refresh. Existing private forks
are untouched. Legacy repositories created before the public forge may have
unrelated Git roots: migrate changes deliberately into a new public-source fork
before submitting a pull request; never rewrite or silently discard that history.

To check out optional source for development, explicitly run:

```sh
python scripts/checkout_store_apps.py --store-url https://staging.aristoteleo.com --destination /path/to/app-repositories
```

`export_store_apps.py` is the one-time tracked-source extraction tool. Official
publication uses Hub's `scripts/publish_official_apps.py`, which requires an
administrator and refuses to replace an existing tag or official identity.
