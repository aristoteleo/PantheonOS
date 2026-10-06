# Local General Team product preset

The opt-in local product supports `"preset": "general-team"` in the private
`--setup` JSON. CLI and the native Desktop candidate use the same expansion and
ordinary App profile compiler. This is a new-profile preset, not an importer that
rewrites an existing user's custom dependency configuration.

The preset supplies Files, per-logical-Agent Shell sessions, Notebook, Web,
Evolution, Desktop, Fleet and Model Services management. It binds background
memory and project GUI file access independently of individual Agent sessions.
It keeps the packaged General Team and plugin defaults. Explicit user settings
remain unchanged. It does not select models, download model weights, create a
cloud account, import another application's credentials or start paid compute.

## Owner input

| Field | Required choice |
| --- | --- |
| `protocol` / `preset` | `1` / `"general-team"` |
| `agent` | Original Agent `protocol`, `namespace`, `projects`, `models`; optional `settings`, `active_project`, `default_project` |
| `models` | Explicit Model Services access policy: `deployments`, `routes`, `allow_wake` |
| `model_apps` | Ordinary attached model publications, each with `deployment_id`, `name`, `models`, `app` |
| `files.sampling` | Model reference, `max_tokens`, `max_requests_per_call` |
| `files.image_generation` | Model reference, `aliases`, `timeout_seconds` |
| `desktop` | `user_seed`, `catalog`, `store`, `data`; optional `data_roots` and Store `credentials` |
| `evolution` | `execution`, `options`; optional isolated `placement` and its `credentials` |
| `credentials` | Optional explicit Agent vault references, including original BYOK/platform-budget bindings |
| `management` | Optional Hub vault reference `hub` and `hub_ca_pem` for cloud management |
| `notebook` | Optional `execution_timeout` and `execution_logging` |

Use `{"$local":"workspace"}` for a project attached to the launcher's workspace.
Do not supply generated Agent `dependencies`, `auxiliary` or `view_dependencies`
in this format. The compiler rejects conflicts instead of overwriting them.
Keep using the existing detailed setup format for custom tool/MCP compositions.

If Agent models use `fleet_tiers`, explicitly bind **low, normal and high**.
This avoids foreground chat succeeding while background memory fails on its
separate low-tier model. The three tiers may intentionally use the same selected
model; the preset never fills a missing tier by silently substituting another.
BYOK, platform-budget and OAuth configuration retain the original Agent schema.
Availability and capability checks still belong to the model service/runtime.

Files references must be present in the chosen model access policy. The preset
constructs its own model-access App with only the selected deployments/routes,
not every model available to the Agent. Sampling and generation require their
corresponding model capabilities. Model selection does not launch an engine.

Without `management.hub`, local model management still shares the same persistent
directory as inference. Cloud operations are explicitly unavailable; this is not
an empty observation of cloud resources and does not stop remote models.

## Product release

The bundle must contain the normal `agent`, `allocator` and `model-access` Apps,
plus these aliases:

| Alias | App id |
| --- | --- |
| `files` | `file-manager` with sampling and image generation |
| `shell` | `shell` |
| `notebook` | `integrated-notebook` |
| `web` | `web` |
| `evolution` | `evolution` |
| `desktop` | `desktop` |
| `fleet` | `fleet` |
| `model-management` | `model-services-management` |
| `files-models` | `model-services-control` |

Also include each explicitly selected model App alias. The Agent release must
declare the corresponding versioned dependencies, including startup Files access
and runtime Shell/other tool access. The preset does not modify immutable App
manifests. It compiles the complete selected provider interfaces, including
explicit hidden Files metadata access, using the existing tool-contract compiler.

## Launch and current limits

Keep setup JSON private (`0600`). An existing built local bundle can be launched
through the opt-in terminal path:

```sh
pantheon cli --bundle /absolute/product --setup /absolute/setup.json \
  --profile /absolute/profile --workspace /absolute/workspace
```

The native Desktop candidate's `PANTHEON_LOCAL_AGENT_CONFIG` launch file uses the
same `bundle` and `setup` paths and the same profile owner. Its `launcher` remains
an explicit executable/argument array, e.g. an installed Python with
`["/absolute/python", "-m", "pantheon"]`. Do not run two owners of the same
profile at once. Closing the native owner drains its Apps; the profile only
reopens automatically after a verified clean stop.

This removes manual graph/grant/schema wiring from the setup file. It does not yet
provide a graphical first-run model selector, migrate existing data or switch
legacy CLI/Desktop defaults. Full cross-node, release/rollback and default
production acceptance remain separate gates.

## Verification

The native General Team gate exercises four lifetimes of the same profile: two
through the profile owner API, a resumed streamed call through `pantheon cli`,
and a call through the native Desktop control/HTTP-view entry point. It verifies
real Shell output, stable Agent identities, history and clean owner shutdown.
Text/image upstreams are deterministic fixtures. Compiler tests separately cover
complete provider selection, narrow Files model policy, preserved owner choices
and compilation without importing Agent runtime. Rendered installed-Desktop and
live-provider acceptance remain outstanding.
