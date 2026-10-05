# Agent execution as an ordinary App dependency

The independent Agent package now provides `agent-execution@1`. It runs the same
`Agent`, `PantheonTeam`, fresh `Memory`, compression plugin and explicit model
scope as the interactive Agent. It does not install a second inference engine,
instantiate local Files/Shell, or discover another Agent on failure.

This is the execution service needed by the Evolution extraction. Evolution's
production worker composition has **not yet switched** to it. The caller-side
durable tool dispatcher, action/evaluation budgets, turn wind-down, analyzer and
feedback composition, sandbox mode, generic Fleet deployment recipe, full default
Team acceptance and product cutover remain required.

## Ownership and authorization

The consumer App owns its workspace, tool implementations, evaluator and actual
tool effects. Agent owns reasoning, model access, its private execution journal,
per-run memory and background orchestration. Tool requests travel as data through
the dependency RPC channel. There is no callback URL, passed credential, Python
code loading or caller-controlled local tool factory in the Agent service.

The owner declares an ordinary runtime dependency on `agent-execution@1` and uses
`execution_method_rules(logical_consumer_id)` from
`pantheon.apps.agent_execution_client` when assembling the existing generic
dependency grant. Every method **binds `consumer_id` at the gateway**, removing
that field from caller-controlled arguments. The ID must come from the owner's
durable logical identity; an HTTP field alone is not authentication. The existing
issuer still binds exact provider and consumer deployment generations. Do not
give consumers the native host token or an unfiltered GUI/owner grant.

Use runtime binding when an Agent calls an App that itself needs Agent execution,
so an Agent/Evolution dependency does not become a circular startup prerequisite.
Grant revocation, renewal and release placement remain owner responsibilities;
this increment does not introduce a new Fleet transport or authorization system.

The service uses its already-delivered Model Services scope. Model refresh and
validation happen before inference. There is no credential/environment fallback
when a model is unavailable or revoked. Each execution gets fresh memory and an
image resolver restricted to that memory's image directory, not another chat's
images. Complete media lifecycle/export acceptance remains part of the migration.

## Request and tool protocol

The SDK has no Agent, settings, Model Services implementation, platform runtime or
provider SDK imports. Construct `AgentExecutionClient` with an existing prepared
`DependencyClient`; it never obtains or renews credentials itself.

1. Persist an `execution_id` in the consumer's own run journal before `submit`.
   A specification contains `prompt`, `instructions`, `model`, optional `tools`
   (named groups of OpenAI function schemas), `max_turns`, `timeout_seconds` and `turn_messages`.
2. `submit` durably admits one request. Repeating the same ID and normalized
   specification returns its status; a different specification with that ID is
   rejected. No automatic retries are performed by the SDK.
3. `poll` returns state, the number of pending tool outcomes and one tool request.
   Queued calls are returned before already-claimed calls so parallel tools can
   progress. Observation alone never authorizes executing a tool.
4. Persist a tool operation intent, then `claim` its `call_id` using a durable
   `worker_id`. Execute only after a positive claim receipt, through the consumer's
   own authorized tool provider. A recovered claim is **not** permission to rerun
   an operation: consult the consumer's tool ledger and reconcile its outcome.
   Another worker cannot steal a claim after a crash.
5. Persist the tool's outcome before `reply`. Replies are either
   `{"ok": true, "value": ...}` or `{"ok": false, "error": "..."}`. Identical replies
   are idempotent; conflicting replies are rejected. Agent only resumes after the
   reply digest is committed. Late replies can reconcile a stopped execution.
6. Once completed, `read_result` returns bounded base64 fragments of the serialized
   `AgentResponse`, including message/cost metadata. The SDK verifies offsets,
   total size and SHA-256 before returning the assembled JSON.
7. `release` discards the result only after execution has ended and all claimed
   tool outcomes have settled. Request and reply digests remain as tombstones,
   preventing a delayed submission/reply from recreating work.

An ambiguous transport error preserves the original IDs. Poll or reconcile them;
do not create a new ID just because an observation timed out. The SDK joins its
local transport thread before propagating cancellation. Closing the SDK does not
cancel a remote execution.

## Cancellation, restart and limits

`cancel` stops inference and withdraws queued tool requests. Claimed tool effects
belong to the consumer and cannot be killed by the Agent service. A `cancelled`,
`failed` or `interrupted` execution can therefore still have `pending_tools > 0`.
The consumer must stop/drain its own Shell, Python, file writes or remote tasks
and report their outcomes before treating its own App stop as complete.

Agent App shutdown joins its engines and plugins. It retains outstanding consumer
claims without asserting those effects stopped. Plugin/journal cleanup failures
fail App shutdown. Restart reports previously active requests as `interrupted`;
it never replays inference or tools, and a late tool reply does not restart them.

Limits currently include 16 active requests, 2048 retained requests before
release, 256 KiB specifications, 192 KiB tool arguments/results, 64 outstanding
tool requests per execution, 10000 tool request records, a 24-hour
maximum execution deadline, 16 MiB results and 32 KiB result fragments. Oversized
payloads fail explicitly rather than being silently truncated. These bounds do
not replace resource or billing policy at the consumer/provider.

`max_turns` defaults to 40 and accepts integers from 1 to 1,000,000 or `null`.
It uses the existing Agent history-message limit, not a count of tool rounds.
`null` removes that count limit but keeps the required finite deadline (default
600 seconds, maximum 86,400). `turn_messages` is an optional list of at most 16
objects containing exactly `turn`, `content` and `repeat`. Turn indices are
strictly increasing integers from 1 to 1,000,000; content is 1–4096 characters;
repeat is a boolean. A message is injected ephemerally on that model turn, and
on subsequent turns if repeat is true. These are declarative reminders, not
executable hooks. Invalid reminders are rejected before request admission.

## Evidence and remaining acceptance

### Caller-owned dispatcher

`pantheon.apps.agent_execution_runner.AgentExecutionRunner` implements the caller
side with the existing `AgentExecutionClient`, an explicit logical binding ID and
an async authorized tool callback. It imports no Agent, provider SDK, Model
Services implementation or platform authority. Its private SQLite ledger holds
execution specifications, stable per-call worker identities, execution fences
and result receipts. A lifetime file lock prevents a second process from taking
over the same local journal while accepted tools are running. This does not fence
independent replicas or make arbitrary callbacks transactional.

Cancelling a `run()` observer leaves its driver owned by the dispatcher. The
application explicitly calls `cancel()` or `close()` to stop inference and join
all accepted tools before closing their underlying resources and the borrowed
client. By default tools drain; only declared cancellable tools receive callback
cancellation. Their callback must join its effects before acknowledging that
cancellation. The receipt writer itself is never cancelled. Callbacks return
protocol outcomes; exceptions mean effects are uncertain and prevent a clean
stop/new work. Ordinary known tool failures use `{ok: false, error: ...}`.

An execution fence is committed before entering a tool. A previously saved
reply can be resent after reconnect, including when the Agent already accepted
it. A recovered claim permits execution only if the exclusive caller journal
proves the callback never crossed that fence. A crash after the fence but before
a durable result leaves an unknown outcome; the dispatcher refuses replay. A
new caller must reconcile saved active identities before admitting replacement
work. No automatic retry loop, claim stealing or new execution ID is introduced.

After archiving a terminal result, `release()` clears result/tool bodies and
in-memory task references, retaining the execution identity tombstone. Uncertain
or active tool effects cannot be released. Release itself remains owned if its
observer disconnects. Interrupted effects currently require owner reconciliation;
there is no generic UI or automatic inference of what an arbitrary tool changed.

The native process test now also uses this dispatcher and the real execution SDK
for three Agent model turns, an actual file edit and a Python evaluation
subprocess. Reopening both sides returns the saved result with exactly one edit,
one evaluation and three upstream model requests. A separate subprocess exits
abruptly after a disk write; reopening its ledger refuses tool replay. Tests
also cover parallel tools, lost reply receipts, stop/claim races, repeated
cancellation, blocked synchronous writes, persistence failures and release.
Upstream model output and grant delivery remain controlled fixtures.

Evolution's opt-in single-agent binding now uses this dispatcher with owned
Files/Python/Shell and its original evaluator/submit/archive callbacks. It saves
mutation identity, budgets and submission/best-result state, fences workspace
reset and holds the journal lock through actual tool shutdown. The native test
uses an independent Agent process and forbids local Agent construction in the
Evolution caller. The combined suites passed 114 tests, no skips, in 33.18s
(`/tmp/evolution-ordinary-agent-combined.log`). Model replies and grant delivery
are fixtures. The production composition has not switched; interrupted Evolution
checkpoint/archive recovery, analyzer/summarizer/sandbox composition and complete
tool context bindings still need implementation. Restoring a generic tool reply
alone does not recover the archive or authorize another mutation.

Evolution's default feedback reviewer also uses the ordinary execution service
when the remote binding is supplied. It keeps its own request/result journal,
saves outcomes before releasing generic receipts and blocks fresh evaluation
when an earlier helper call is unresolved. Confirmed inference failures preserve
the original evaluator fallback; persistence/transport ambiguity propagates a
recovery error. Stop/cancel joins inference and pending receipt writes/releases.
The 121-test combined gate (`/tmp/evolution-feedback-combined.log`, 37.44s, no
skips) includes real independent Agent-process reviews and concurrent Evolution
workers. Upstream model replies and grant delivery remain fixtures; whole-run
archive recovery and production composition are still incomplete.

Focused tests cover request/reply deduplication, competing claims, caller
isolation, parallel requests, timeout/cancellation, late outcomes, interrupted
restore without replay, persistence failure, cleanup failure and private memory/
image authority. A native HTTP App subprocess performs actual Agent model turns,
caller-side file modification and evaluation. Only the model output is a local
HTTP/SSE fixture. A clean built release with no source checkout also uses the
original Model Service Connector, recovers a result after restart without another
inference call, and refuses inference after its model grant is revoked.

These tests do not prove whole Evolution run recovery, native
Fleet execution-grant issuance, cross-node failure recovery, paid model behavior
or full P0–P7 completion. No installed Fleet/Atrium, default entry or deployment
was changed by this increment.
