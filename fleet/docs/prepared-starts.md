# Prepared resource starts

`app-start-preparation=1` advertises owner-authorized, node-local admission before
process launch. A group coordinator must negotiate it on every selected node.
This primitive is implemented; a complete multi-node SGLang coordinator and
network transport are not yet available.

1. Stage and install the exact immutable package on each node. Its manifest
   declares all component budgets; a prepare request cannot substitute budgets.
2. Persist caller-chosen operation IDs before sending `prepare_start`. It uses
   the existing lifecycle submit envelope with exact digest, scope and current
   generation. It requires an absent/stopped resource-free instance and an
   installed artifact. It creates a `prepared` instance at generation + 1.
3. Inspect each operation and instance. On missing acknowledgements, keep the
   outcome unknown and inspect the same operation ID; never create another
   preparation. No process, hook or dependency startup runs during preparation.
4. Only after every member is acknowledged, submit `start` concurrently to all
   nodes, supplying `start_preparation_id` and each prepared generation. Start
   atomically transfers the existing reservations to the next process generation
   before launching; it does not double-count them or briefly free them.
5. To abort an unconsumed hold, submit `stop` with the prepared generation. It
   releases the hold without stop hooks. Once start has consumed the hold, that
   old generation fails CAS. Inspect and explicitly stop the current generation;
   reservations remain until its processes are confirmed stopped.

Prepared holds have no automatic expiry. They survive Runner restart,
observation and reconciliation. This avoids a coordinator/network timeout
releasing admission underneath delayed starts. Identities are owner/node scoped;
these are not inference grants or distributed atomic commits. Partial group
start, cross-node recovery and reconciliation remain coordinator obligations.

The first successful preparation or start fence migrates the **on-disk ledger** from version 1
to 2. Older Runners reject it instead of mistaking a process-free reservation for
dead state. Existing nodes that never use these primitives stay on ledger version 1.
The lifecycle request, manifest and snapshot wire protocol remain version 1.
Do not downgrade a node that has used prepared starts to an older Runner or edit
its ledger to bypass the guard. No automatic downgrade migration is provided.

## Fencing a possibly delayed request

`app-start-fence=1` advertises the owner-only lifecycle `fence_start` method. Its
`request` is the complete original `prepare_start` or prepared `start` request,
including original operation ID, digest, scope, generation and preparation ID.
This is a separate method, not a new action with a new ID.

Under the same durable lock as `submit`, Fleet either returns the exact existing
operation or persists a terminal `cancelled` operation without code, hooks or
resource changes. If submit won the race, no accepted work is cancelled. If the
fence won, all delayed matching submits return that cancellation. Different
requests cannot reuse the ID. Keep these records for the ledger's lifetime; a TTL
or deleting them would permit a delayed request to execute.

Cancellation alone does not release a prepared hold. Observe the cancelled
start and stop its exact prepared generation using the normal lifecycle path.
A missing stop request may be redelivered under its same ID/generation; accepted
operations must instead be observed. This closes the claim-before-send crash
gap without replaying a start or assuming a timeout means no process exists.

These holds reserve admission budgets; they do not establish authenticated
NCCL/Gloo interconnects, open listening ports, prove cross-node model identity,
publish inference endpoints or replace real multi-node GPU acceptance. Those
must be completed before enabling a user-facing multi-node launch action.
