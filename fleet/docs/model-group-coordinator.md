# Durable coordinated starts

`pantheon.models.group_coordinator.GroupCoordinator` builds on Fleet's
`prepare_start` protocol. This is an internal lifecycle primitive. It is not yet
a published multi-node model deployment API, SGLang topology implementation or
inference route. Single-node Model Services behavior is unchanged.

## Storage and ownership

Production uses `HubGroupJournal(client, fleet_owner)`. The owner-authenticated
`/api/model-services/groups` API stores intent in Hub's `model_groups` table,
independently of Agent pods. The additive table is created by Hub's existing
metadata initialization. Each user has isolated group IDs and a 128-group limit.
There is no delete endpoint or automatic retention cleanup while recovery may
still depend on these records. No credentials or arbitrary extra fields are
accepted in group documents.

Hub enforces immutable targets/requests, monotonic delivery claims and the
prepare-all barrier. Updates use a revision CAS; concurrent stale writers cannot
claim the same RPC. PostgreSQL serializes new-group admission on the owner row.
A lost commit acknowledgement is propagated before any Fleet mutation. Recovery
loads the original group ID; it never falls back to an Agent-local journal or
creates a replacement intent. Observations are reported by the owner coordinator,
not independent Hub proof of inference readiness or authorization to adopt work.

`GroupJournal(path, owner)` remains the synchronous SQLite implementation for
local lifecycle acceptance. Its path must be explicitly persistent. Both stores
use the same coordinator; all coordinator operations, including `stop`, are async.

Create a group from 2–16 unique explicit Fleet nodes. Each member pins its
installed artifact digest, scope and current stopped generation. All preparation
and start operation IDs are committed together before the first remote mutation.
There are no caller-supplied resource overrides: Fleet uses installed manifests.

The local `save` uses SQLite FULL synchronous commits and revision CAS. Targets and
operation requests are immutable, delivery claims cannot be cleared, and stop
intent cannot return to a start phase. Multiple coordinator workers may observe
one journal; only the winner of a durable claim can send its operations.

## State machine

1. **preparing**: claim and submit preparation requests; observe exact Fleet
   operations and owner/node/digest/scope/generation-bound instances.
2. **committing**: entered only after every member has a confirmed preparation.
   On the next observation, claim starts and submit them concurrently. A failed
   member initiates group cleanup; no replacement rank is chosen.
3. **ready**: every exact start succeeded and its owned generation is ready.
   This does not itself publish an inference endpoint. Continued observation is
   necessary; offline/uncertain members withdraw the group from ready.
4. **aborting**: an explicit stop or observed failure blocks further starts.
   Stop only confirmed owned generations, including untouched preparations and
   partially started processes. Fleet's normal stop hooks remain authoritative.
5. **stopped**: all members have confirmed resource-free outcomes. There is no
   automatic restart of this intent; create a separate explicit group for that.

`advance(group_id)` performs bounded observation and at most one mutation wave.
The caller schedules subsequent observation; no background tasks or poll loop
are created by the library. `stop(group_id)` records stop intent immediately.
No synchronous operation waits for a complete distributed startup.

## Uncertain outcomes

A timeout does not prove the node failed. Starts are not replayed. A lost
reply is resolved by reading the exact operation ID from Fleet. Queued/running
operations block cleanup of that member. After Runner restart, cleanup may stop
the exact generation owned by a terminal interrupted start; it never adopts a
newer generation or bypasses normal stop hooks.

A crash between committing a delivery claim and sending its RPC leaves an
intent whose operation may be missing on the node. During startup that member
remains unknown. On group abort, a valid owner/node snapshot with a missing
operation causes `fence_start` with the **complete original request**. The node
atomically returns the already-accepted operation or durably records `cancelled`
before any delayed copy can execute. The coordinator observes that record before
releasing the preparation or declaring this member clean. A lost fence reply can
be safely resolved by observation or another identical fence.

No fence is inferred from an offline node, elapsed deadline or failed RPC.
Old nodes without `app-start-fence=1` reject the method and remain pending. Fences
are retained across node restart and are never aged out. A recorded cancellation
with a contradictory running generation is treated as a conflict, not authority
to stop that process. The owner-only Agent RPC `model_services_groups` exposes `list`, `inspect`,
`stop`, and `continue_stop`. Listing/inspection makes no Fleet lifecycle calls.
`continue_stop` requires a previously persisted abort; neither action can start
members. Each call performs one bounded observation/mutation wave. There is no
implicit timer, automatic resume, or user-facing group recovery UI yet.

If the missing operation is a **stop**, the coordinator may redeliver its exact
recorded ID, request and generation. This cannot launch work, and generation CAS
prevents stopping a replacement. Fleet's durable operation deduplication prevents
executing accepted stop hooks twice. Existing queued/running/unknown stop records
are inspected and not replayed with fresh IDs. Accepted failed stops remain for
inspection; the coordinator does not bypass hooks or silently force a retry.

Missing/corrupt identity, an unrelated live scope, changed request, failed stop,
or newer instance generation blocks that member rather than stopping it. There
is no cross-node atomic rollback claim; a partition can leave a group pending.

## Validation

Python tests cover the barrier, lost acknowledgements, journal reload, capacity
failure, partial startup, offline and queued ranks, claim-before-send crashes,
concurrent workers, delayed send during stop, persistence failure, identity
fencing, Runner restart and journal revision/owner/immutability checks.

The opt-in cross-language test runs two real isolated Go lifecycle managers with
`NativeDriver` CPU processes. Python uses the real status/submit wire protocol,
discards successful replies, reopens its journal, and stops the owned processes.
It also exercises actual readiness failure, insufficient resource admission,
crashes before preparation/start/stop submission, lost cancellation replies, and
delivery of the original request after its group has finished cleanup.
The Go test verifies both process death and empty reservations afterward.

```sh
python -m pytest tests/test_model_groups.py tests/test_app_lifecycle.py -q
cd fleet
PANTHEON_TEST_PYTHON=/absolute/path/to/python go test -p 2 ./internal/lifecycle -run TestPythonGroupCoordinatorNativeProcesses -count=1 -v
```

These are two managers on one test host, not a network partition experiment or
multi-node GPU acceptance. Private authenticated interconnect, explicit rank
topology, model-group creation/recovery UI, supervised recovery, and real
multi-node GPU execution remain separate gates. No NCCL ports are exposed by
this primitive.

The same six native-process scenarios can run through the real Hub FastAPI
router and Runtime `ModelServices` HTTP client. This replaces Hub application,
DB engine and Agent journal objects while retaining only Hub's SQLite test DB.
It proves restart recovery across the API boundary, not deployed PostgreSQL
availability or multi-node GPU networking:

```sh
# A Python environment with both Hub and Runtime dependencies is required.
PANTHEON_TEST_PYTHON=/absolute/path/to/hub/python \
PANTHEON_GROUP_HUB_SOURCE=/absolute/path/to/hub/source \
go test -p 2 -race ./internal/lifecycle -run TestPythonGroupCoordinatorNativeProcesses -count=1 -v
```
