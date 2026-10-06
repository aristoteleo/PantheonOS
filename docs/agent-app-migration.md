# Local Pantheon-Agent migration

This owner-side entry point prepares an ordinary local App profile, imports
reviewed legacy data, then stops that profile without starting its Agent. Open
the same saved launch through normal CLI/Desktop afterward. The installed legacy
application and its data are not automatically discovered or modified.

The destination release must advertise the Agent data-initialization reservation
protocol. Older releases that could treat an interrupted import as a fresh Agent
must be updated before this workflow is used. Source exclusion is cooperative:
updated legacy runtimes hold shared data leases, but older binaries, independent
replicas and external editors must be stopped separately.

## Request and invocation

Create a private JSON request (mode `0600`) with explicit source paths and stable
project identities. Use the same reviewed snapshot as the inventory command;
do not invent replacement project IDs. For example:

```json
{
  "protocol": 1,
  "operation": "agent-import-001",
  "app": "agent",
  "legacy": {
    "projects": [{"id": "existing-project-id", "name": "Research", "path": "/absolute/project"}],
    "active_project": "existing-project-id",
    "default_project": "existing-project-id",
    "home_memory": "/absolute/legacy-memory",
    "global_config": "/absolute/legacy-user-config",
    "project_config": "/absolute/project/.pantheon"
  },
  "backup": "/absolute/private-backups/agent-import-001",
  "retained_roots": []
}
```

The `legacy` object also accepts the inventory's explicit memory overrides,
external Agent libraries, environment file and runtime handoff files. The backup
must be outside inventoried source data and separate from destination Agent data.
The default backup byte limit is 20 GiB; an explicit positive `max_bytes` in the
request selects another limit. Backups retain opaque configuration/credentials
privately; public progress never includes their values or conversation content.

For a saved local launch:

```sh
pantheon agent-migrate --launch /absolute/launch.json --request /absolute/migration.json
```

For an explicit product bundle:

```sh
pantheon agent-migrate --bundle /absolute/product \
  --setup /absolute/setup.json --profile /absolute/profile \
  --workspace /absolute/project --request /absolute/migration.json
```

Optional `--credentials` uses the existing local profile credential delivery
mechanism. This does not itself convert legacy credentials. The command reserves both model providers and consumers before initializing
data. Installation hooks run, but neither model-provider nor consumer backend
processes start during migration. Prepared bindings name their future identities;
they do not assert that a model is already available.

The request's `app` selects the actual installed Agent artifact. The migration
resolves its data directory and Files/Shell bindings from the prepared instance;
it does not accept an arbitrary destination directory. The request, profile,
owner and stable target identity are bound together by a private reservation.

## Retained environments and models

`retained_roots` explicitly selects disjoint workspace directories or execution
subdirectories under individual legacy tasks. They remain on this local node;
the prepared Agent must have ordinary `file_manager` and `shell` profiles pinned
to this node. Whole Agent/configuration roots and directories containing owned
`task_state.json` are rejected. Retention does not make an environment portable
to a different OS, filesystem or node.

Optional `model_selection` accepts the existing conversion fields:
`selections`, `fleet_tiers`, `dependency`, `templates`, `settings`, `budget_choice`
and `source_service_id`. Saved member selections must name the original
conversation/config IDs and explicit Model Service targets. All quality tiers
must be mapped, reasoning effort and fallback order preserved, and the prepared
Agent's model configuration must match. No model or provider is silently chosen.

Optional `model_credentials` migrates backed-up BYOK keys to the current local
Fleet vault. It requires `model_selection`; keys belong to the model Connector,
not to the Agent. For example, alongside the reviewed model mappings:

```json
{
  "model_credentials": {
    "bindings": [{
      "provider": "openai",
      "source": "/absolute/project/.pantheon/settings.json",
      "alias": "connector",
      "endpoint": "https://api.example/v1",
      "ref": "node-secret://migrated-provider"
    }]
  }
}
```

The alias must identify a selected `model_apps` Connector whose `secret_ref` and
endpoint exactly match. The source must match the effective key in the captured
settings/environment. No key value is placed in this request, the public result
or Agent settings. Existing conflicting vault entries are not overwritten.
`global_fallback` accepts the existing explicit `{credential: binding-or-null}`
conversion for the captured `LLM_API_*` pair, with the same Connector target check.
After migration, normal startup uses these vault entries; no separate hand-copy
of legacy model keys is required.

Platform-budget provisioning/review and MCP conversion remain separate owner
APIs and are not yet connected to this command. Such legacy inputs remain
blockers rather than being dropped. Complete
OAuth/configuration coverage, remote data placement and distributed cutover are
still outstanding; this entry point is not a general production migration wizard.

## Failure, retry and cancellation

The destination reservation is written before the old source fence is acquired.
If backup or conversion fails before an import state exists, normal Agent startup
still refuses that destination. The same immutable request can resume and reuse
verified backup blobs. A changed request, profile, owner or target cannot adopt
that reservation.

While the command is waiting on an error, `SIGUSR1` retries its same operation.
`Ctrl-C` or `SIGTERM` requests ordered profile shutdown; it does not release old
source fences or pretend the migration succeeded. File-copy work is drained before
cleanup; it is never detached while the host exits. An unexpected owner crash uses
the existing local profile recovery path before reopening the same request.

To explicitly abandon an uncommitted migration, drain its current owner first,
then run the same command and request with `--abort`:

```sh
pantheon agent-migrate --launch /absolute/launch.json \
  --request /absolute/migration.json --abort
```

Abort marks the destination unusable before releasing old source fences. It
retains the immutable backup and any partial destination data. Repeating abort
is idempotent. Starting a new migration after abort requires a new destination
profile. Committed imports are refused by this operation: post-cutover rollback
must account for new user writes and is a separate, unfinished workflow.

On success, standard output contains one JSON result with the data location,
backup reference and conversation count, after the profile has stopped. Progress
and errors go to standard error. This does not run a conversation or demonstrate
that every configured external provider is available; subsequent normal startup
performs ordinary Agent dependency admission.
