# Durable model-group creation intent

The pre-launch creation controller records an immutable plan in the Hub before
requesting a node-owned authority or constructing rank artifacts. This is an
internal foundation. It is not yet wired to the user-facing group creation flow
and does not install, start, or publish inference services.

## State and effects

`CreationJournal.create(plan, source_sha256)` persists only public intent through
`PUT /api/model-services/group-creations/{group_id}`. The Hub validates the exact
owner, private addresses, rank order, started generations, engine recipe, resource
assignments and plan-derived topology hash. The source digest pins the package
inputs. Credentials, PEM material and arbitrary extra fields are rejected.

Each explicit `CreationCoordinator.advance(group_id)` performs at most one step:

1. `intent` becomes `building`, recording that the original authority may be
   requested. This step returns after the Hub acknowledges the claim, before RPC.
2. The original rank-zero node prepares its idempotent authority. The controller
   verifies its owner, node, group and topology, and binds the returned public
   certificate to its SHA-256 pin. Only the pin is persisted.
3. An injected builder constructs the first missing original rank artifact and
   returns a content digest. All ranks recorded changes the phase to `built`.

`GroupPackageStore` supplies the concrete builder. It verifies `source_sha256`
and reproduces content-addressed Fleet packages from the pinned inputs without
installing or starting an engine. Package availability at a node and actual
resource/network enforcement remain separate admission checks.

`stop` durably moves the intent to `aborting`. Advancing then closes the original
authority if its delivery was claimed, even if no public root reply was received.
The node's close-before-prepare fence prevents a late prepare from reopening it.
If authority delivery was never claimed, cleanup has no node side effects. The
intent becomes `stopped` after the required close acknowledgement.

Every mutation uses a revision compare-and-swap. A timeout never implies success
or failure: reconstruct the client and load the original ID. An authority request
can retry only against the same idempotent node/topology. Lost artifact save
acknowledgements are resolved from the journal before choosing another rank.
Cancellation rejects stale results, including a build that finishes after stop.
This controller never replays an engine start.

## Original source and rank packages

Before creating the Hub intent, call `GroupPackageStore.capture(snapshot_record)`
with a verified prepared snapshot receipt. Put the store on durable workspace
storage. This captures the installed compiler, supervisor, pinned image recipe
and model file identities; it stores no model weight bytes or credentials.
Use the returned digest as `source_sha256` and pass the store as the coordinator's
builder. Missing or corrupt original source requires restoring those exact bytes;
it never substitutes a newly installed Agent's source.

Compilation runs in a bounded child using the saved compiler and a clean import
path/environment. It validates every rank's resource budget and plan before
generating one deterministic tar, then atomically publishes it in the local code
cache. Cancellation/timeout kills and reaps this child before removing temporary
files. `artifact(digest)` verifies and returns the bytes for later Fleet staging.

Each rank has a separate manifest with its own resources, sealed group-peer
binding, a read-only prepared-weight mount, an immutable image, and authenticated
status. No install hook downloads weights or dependencies. The entrypoint compares
the node's model receipt with the pinned package; unchanged cache timestamps avoid
rescanning weights, while timestamp drift requires matching full content hashes.

Packages require `model-group-private-network`, which current bridge-only nodes
do not advertise. Fleet rejects them before dependency preparation. This is an
explicit integration gate, not a network implementation or an instruction to
enable host networking. The admission driver must provide the pinned private
interface/address and collective isolation before this capability is advertised.

## Persistence and identity

The additive `model_group_creations` Hub table uses the existing database startup
schema creation. Records are owner-scoped and survive Agent replacement. Initial
admission shares the owner's PostgreSQL row lock with the existing group journal;
the two APIs cannot claim the same group ID. Stopped IDs remain reserved and the
current admission limit is 128 creation records per owner. There is no delete or
implicit ID reuse endpoint.

## Atomic lifecycle handoff

`CreationJournal.handoff(group_id)` calls the owner-scoped
`POST /api/model-services/group-creations/{group_id}/handoff` with the observed
creation revision. The Hub transaction inserts the initial lifecycle journal and
marks the creation `handed_off` together. It derives each target from the original
rank/node/artifact, uses scope `model-group-{group_id}`, and converts the pinned
started generation to the pre-prepare generation (minus two). Prepare/start IDs
are SHA-256 of the canonical JSON array `[owner, group_id, source_sha256, rank,
action]`, where action is `prepare` or `start`. Clients cannot substitute targets,
operations, trust roots or readiness acknowledgements. No RPC occurs here.

Only `built` may transfer. Ordinary PUT cannot create or modify `handed_off`.
Concurrent cancellation and handoff use the same creation revision: either cancel
wins and no lifecycle row exists, or transfer wins and stopping belongs to the
lifecycle journal. Insert failure rolls back the creation update. An acknowledged
transfer permanently preserves both records. The ordinary group create endpoint
continues to refuse IDs reserved by a creation.

If the response is lost, reload the same ID and retry handoff. It returns the
original creation and the *current* lifecycle journal, including a later abort or
stop; it never resets operation IDs or progress. Runtime checks that the returned
targets/requests/trust still match the original creation. A missing original
lifecycle row is an error, never permission to create a replacement.

After transfer, `CreationCoordinator.stop` persists abort through the lifecycle
coordinator; it does not close the authority while ranks might own resources.
Subsequent lifecycle observation/cleanup belongs to `GroupCoordinator.advance`.
Creation `advance` has no effects after handoff. Integration must stage/install
original packages and satisfy private-network admission before advancing starts.
`built`, `handed_off` and lifecycle `preparing` are not readiness or launch proof.

## Validation and remaining integration

Hub tests cover ownership, strict plan validation, immutable inputs, separate
effect barriers, concurrent revisions, cancellation, and cross-journal identity
collisions. `tests/test_model_group_creation_hub.py` combines the actual Runtime
client with the Hub ASGI API and a persistent SQLite database, recreating clients,
applications and connections between steps. Authority delivery is a controlled
double. Package recovery is also exercised with the real saved compiler and a
prepared tiny model fixture; it does not launch Fleet nodes or run inference.
A Go boundary test consumes the generated tar and proves that missing-network
admission precedes dependency or engine effects.

Run the boundary test with both checkouts on `PYTHONPATH` in an environment with
Hub test dependencies and `cryptography`. The test is skipped without the Hub
checkout. `tests/test_model_group_postgres.py` additionally runs against an isolated real
PostgreSQL server: ten cross-journal ID-admission races, ten handoff/cancel races,
and four concurrent duplicate handoffs. Each run creates/drops only a unique test
schema; set `PANTHEON_GROUP_TEST_POSTGRES` to a disposable server DSN. This proves
the transaction boundaries against PostgreSQL, not deployment to the Hub cluster.

Remaining gates are controller/UI integration and node staging of these packages,
collective-network admission, leader-only inference
publication, and installed Fleet
GPU cancellation/partition/recovery tests. Private IPs and mTLS control channels
do not by themselves isolate or encrypt NCCL traffic.
