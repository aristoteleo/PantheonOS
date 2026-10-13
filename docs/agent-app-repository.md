# The Agent as an App repository

The Agent is an ordinary App in the Store. It has an official repository with
versions and history. An owner installs that repository into their App
library, develops on a branch, and runs a version of it as their Agent.
Returning to an official release is one action.

Five pieces make this work:

1. a build step any App may declare,
2. the official Agent source repository,
3. installing large Apps into the library from the Store,
4. running a library build as a deployment App, and
5. the Store pages that tie them together.

## 1. App build step

An App repository holds source. Most Apps are their own package: the Desktop
packs the committed tree as it is. An App whose package is assembled from its
source (compiled frontend, pinned binaries) declares a build:

```json
"build": {"argv": ["python3", "build.py"], "timeout_seconds": 1800}
```

The Desktop runs `argv` in a fresh copy of the commit's snapshot with:

| Variable | Meaning |
|---|---|
| `APP_BUILD_OUTPUT` | empty directory the build fills with the runnable package |
| `APP_BUILD_PLATFORM` | target node platform, e.g. `linux-amd64` |
| `APP_BUILD_REVISION` | the commit being built |
| `APP_BUILD_CACHE` | per-App directory kept between builds (downloads, `node_modules`) |

The output must contain an `app.json` with the same App id and a `fleet.json`
execution declaration. It is packaged exactly like a committed package
(`build_artifact`). A build runs in the background (`desktop_app_build`
`start`/`status`). Its result is kept per repository, commit and platform, so
a commit is built once. Installing an App with a build step on a node uses the
finished build of that commit and refuses when there is none.

The build runs the repository's own code on the owner's workspace node, with
the same trust as running the App itself.

## 2. The official Agent repository

```
app.json             source manifest (id agent, version, dependencies, tools …) and "build"
README.md
build.py             assembles the package; standard library only
build.json           credential declarations and pinned prebuilt inputs (below)
execution.json       the process declaration fleet.json is made from
requirements.txt     the locked Python environment
cli.py, backend/__init__.py
backend/pantheon/    the Agent's Python source
fleet-runtime/       the process adapter copied to .fleet-runtime
frontend/            the Agent window's source (Vite project, with .env.production)
```

`build.py` produces the package the release set has always shipped:
`backend/_vendor/pantheon`, `frontend/` (compiled), `.fleet-runtime/`,
`fleet.json` with the Agent's configuration and credentials, and
`release.json`.

Two inputs are prebuilt and pinned by URL and SHA-256 in `build.json`:

* `transport`: the Model Services transport executable, per platform.
* `frontend`: the compiled Agent window, with the digest of the `frontend/`
  source it was built from. When the source in the commit has that digest, the
  build unpacks the prebuilt bundle. Otherwise it runs `npm ci` and
  `npm run build:agent-app` (Node 20) and uses the result.

Editing prompts, templates, skills or Python therefore needs no Node build.
Editing the window's source does; `node_modules` is kept in the build cache
while `package-lock.json` is unchanged.

The release builds the prebuilt window from the exported `frontend/` itself
(`general_agent_release --frontend-source`), with the public build
configuration in `frontend/.env.production`, so the recorded source digest is
the source of that window.

Each Agent release exports this tree, commits it on top of the previous
published version, tags `v<version>` and publishes it as the official `agent`
App (`scripts/publish_agent_repository.py`). The release set's `agent` package
is built by running that same tree's `build.py`, so the official repository at
a tag builds exactly the artifact the release set ships.

## 3. Installing large Apps

A Store release travels as a git bundle. The Agent repository is several
megabytes, more than a browser may send to a deployed Desktop in one request.
The Desktop therefore downloads the release itself from the Store
(`desktop_store_manage` `install` with a package id and version), with the
owner's Store credential. Small Apps keep the existing path.

## 4. Running a library build as a deployment App

A deployment App may name an installed package instead of its release-set
package:

```json
"agent": {"package": "agent", "digest": "<sha256>", "scope": "agent", …}
```

* The package must already be installed on a node of the fleet. The Desktop
  installs it there before changing the deployment.
* It must be the same App as the release set's package (same App id). The
  data schema and dependency contracts are checked as for any start.
* The controller places it only on nodes where it is installed. If none has it,
  the App is blocked with a reason rather than falling back silently.
* `PATCH …/apps/<app>` with `"digest": ""` returns to the release set's
  package. Setting the Agent up again with a release does the same.

Switching follows the usual revision rules. A revision that never ran starts
from a copy of the data of the revision that ran last. A revision that ran
before resumes its own data. So switching back to the official release resumes
the conversations it had. Conversations made while a fork ran stay with that
fork and reappear when it runs again.

## 5. Store

The Agent appears as one App with its sources:

* **Running as your Agent**: the official release, or the commit of your
  library repository that runs now.
* **Get the source**: installs the official repository into the library and
  creates the development branch.
* On a library version: **Run as my Agent** (build if needed, install on the
  Agent's node, switch the deployment) and **Back to official release**.

The rest of the page (branches, versions, publishing, pull requests) is the
same as for every App.
