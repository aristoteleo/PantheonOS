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

The builder must verify `source_sha256`, reproduce content-addressed packages
from the pinned inputs, and must not install or start an engine. The production
builder is a remaining integration step. A callback alone is not proof of source
integrity, package availability, or actual node resource enforcement.

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

## Persistence and identity

The additive `model_group_creations` Hub table uses the existing database startup
schema creation. Records are owner-scoped and survive Agent replacement. Initial
admission shares the owner's PostgreSQL row lock with the existing group journal;
the two APIs cannot claim the same group ID. Stopped IDs remain reserved and the
current admission limit is 128 creation records per owner. There is no delete or
implicit ID reuse endpoint.

A later atomic handoff must bind built digests to the lifecycle journal. Until
that exists, the ordinary group API intentionally refuses IDs reserved by this
creation journal. `built` does not mean ready or running.

## Validation and remaining integration

Hub tests cover ownership, strict plan validation, immutable inputs, separate
effect barriers, concurrent revisions, cancellation, and cross-journal identity
collisions. `tests/test_model_group_creation_hub.py` combines the actual Runtime
client with the Hub ASGI API and a persistent SQLite database, recreating clients,
applications and connections between steps. Authority and package builders in
this boundary test are controlled doubles; it does not launch Fleet nodes.

Run the boundary test with both checkouts on `PYTHONPATH` in an environment with
Hub test dependencies and `cryptography`. The test is skipped without the Hub
checkout. PostgreSQL cross-table concurrent admission still needs real-database
acceptance; SQLite revision races do not prove PostgreSQL row locking.

Remaining gates are production rank packaging, collective-network admission,
atomic lifecycle handoff, leader-only inference publication, and installed Fleet
GPU cancellation/partition/recovery tests. Private IPs and mTLS control channels
do not by themselves isolate or encrypt NCCL traffic.
