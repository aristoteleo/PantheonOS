# App dependency RPC grants

This is the implemented transport authorization contract, not the complete
manifest dependency resolver. An owner coordinator selects exact App instances
and authorizes their relation. Consumers receive only an opaque expiring bearer.

## Issuance and revocation

Hub `POST /api/fleet/apps/dependency-grants` accepts a full owner login or Fleet
management credential. App-instance credentials cannot use it. The request has:

| Field | Meaning |
| --- | --- |
| `consumer` | `node_id`, `instance_id`, immutable 64-hex `revision`, positive `generation` |
| `provider` | Same identity fields, plus `component: backend`, `port: http` |
| `app_id` | Provider App ID, independently checked by the node on invocation |
| `preparation_id` | Optional exact prepared start ID; consumer generation must be its next start generation |
| `methods` | Map from RPC name to `{arguments: [...], bound: {...}}` |
| `ttl_seconds` | 30–900 seconds, default 300 |
| `timeout_seconds` | Per-call ceiling, 1–600 seconds, default 60 |

Hub derives both Fleet identities from the authenticated owner; clients cannot
override them. Method names and caller arguments are exact and case-sensitive.
No wildcard is accepted. Bound argument keys cannot also be caller arguments.
For example, a `read_file` grant can allow caller argument `path` while binding
`workspace_id` to an owner-selected workspace. The provider must resolve the path
inside that workspace and implement its own path confinement.

The response contains `grant_id`, `access_token`, `endpoint` (HTTPS, fixed `/rpc`),
`expires`, `consumer`, and `provider`. `grant_id` cannot be used as the bearer.
Responses are not cacheable. Do not log tokens or put them in manifests, public
ledgers or process argv. A private configured `RuntimeCredential` pairs the
endpoint and access token. Owner-coordinated initial delivery is implemented through prepared App configuration
(see below), followed by owner-managed renewal. These grants do not authorize writing a
remote node's credential vault.

Hub `DELETE /api/fleet/apps/dependency-grants/{grant_id}` is idempotent and limited
to the authenticated owner's Fleet. The Controller endpoints used by Hub are
`POST`/`PATCH`/`DELETE /apps/dependencies`, protected by the Controller service credential.
No management endpoint is exposed through an App wildcard origin.

Hub `PATCH /api/fleet/apps/dependency-grants/{grant_id}` accepts only
`ttl_seconds` (30–900, default 900). Renewal requires owner authentication and
live checks of the original consumer and provider generations. It changes only
the expiry, never the token, identities, methods, bound arguments or call timeout.
The response contains only `grant_id`, `expires`, `consumer` and `provider`.
A retry cannot shorten an existing expiry. A grant that was actually expired,
revoked, or lost on gateway restart returns 410 and cannot be resurrected.
A temporarily unavailable generation returns 409, which is not proof of expiry.

## Calling

Send `Authorization: Bearer <access_token>` to the returned endpoint:

```json
{"method":"read_file","args":{"path":"data.csv"},"timeout_seconds":30}
```

Only these three envelope fields are accepted; `args` and timeout are optional.
Gateway injects bound arguments into a newly serialized Fleet RPC payload. It
does not forward consumer headers, cookies, arbitrary URLs or provider auth.
Existing node-side `invoke` pins App ID/revision/generation, obtains the provider
RPC key locally, and holds its normal in-flight usage lease. Payload and result
are limited to 512 KiB. Large outputs should use separately authorized artifacts.

Python consumer code:

```python
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.dependency_client import DependencyClient

config = load_runtime_configuration(required=True)
files = DependencyClient(config.credentials["files"])
result = files.invoke("read_file", {"path": "data.csv"}, timeout_seconds=30)
```

This is a synchronous client. An asynchronous App must put blocking calls on its
normal worker executor; canceling that observer does not prove the provider
mutation was canceled. It must not automatically replay an unknown outcome.

## Lifetime and limitations

Before issuance, Controller checks the exact provider service and consumer state.
Before each invocation, `app_lifecycle/check_instance` checks consumer liveness
and rechecks ledger state after the probe. The invocation check never includes
`preparation_id`, so a prepared consumer cannot call. A starting consumer can call once its exact
new generation has an actual live owned resource; this permits dependency-based
initialization before readiness. An expired initial credential requires a new
preparation rather than reusing the same immutable configuration. A stopped, dead or superseded
consumer is denied even if the gateway still has its grant. Unknown nodes and
unsupported old nodes fail closed. This also uses the ordinary job-worker control
protocol; real remote HPC acceptance is still pending.

Admission linearizes after the consumer check and a final grant-map check.
Revocation/expiry deny new admission; already admitted calls may finish until the
call timeout. A consumer may stop just after admission without canceling the
provider's accepted write. A transport timeout can mean an unknown outcome. The
gateway and SDK never replay it. HTTP 401/403/409 are authorization/admission
failures; HTTP 502 is conservatively an unknown outcome.

The gateway stores at most 1,024 dependency grants in memory and bounds concurrent
calls using its existing connection budget. Restart loses the grants. There is
no consumer-initiated renewal, automatic provider replacement, direct transport,
streaming grant, session creation, or general distributed identity proof in this
implementation. Initial assembly validates installed manifest interface versions;
this is not runtime schema negotiation. Bearer holders can act within
their grant while its bound consumer is alive. Native Apps continue sharing their
OS user's trust boundary.


## Initial owner assembly

`fleet_app_start_dependencies(consumer, preparation_id, operation_id, bindings,
components)` uses exact installed manifests and the existing prepared-start
protocol. `consumer` contains node/instance/revision and the **prepared**
generation; the coordinator requests grants for generation + 1. Each binding is
keyed by a declared credential alias and contains `app_id`, the consumer
`component`, an exact `provider` (including backend/http), and selected `methods`.
Every method has explicit `arguments` and `bound` maps as above. `components`
contains ordinary declared values and node-secret references, not caller-supplied
dependency bearer keys. The response contains only operation status and the
number of bindings. Use normal lifecycle status to observe completion.

The coordinator checks the consumer manifest's dependency range and `uses`
interfaces against each provider's installed manifest, then issues grants through
Hub and sends them over owner-authenticated node control. Consumers receive the
same `{endpoint,key}` SDK credential shape as local references. The node checks
expiry again before start. Mixed local and dependency credential sources under
the same alias are rejected. Snapshot configuration stays immutable; modifying
bindings means a new preparation, not overwriting a live credential file.

The private POSIX platform journal retains exact input/grants through lost
acknowledgements, clears tokens after configuration acknowledgement, and never
retries a start under a new operation ID. Stable start IDs cannot be reused with
a different recipe. If a stored grant expires before successful configuration,
cancel that preparation using the ordinary stop operation and prepare afresh.
An accepted start is observed even after its grant expires; no inference is made
that expiry means the process stopped. Discarded grants expire naturally and are
also revocable through the existing owner API. Journal loss and cross-replica
ownership are not automatically recovered.

## Owner maintenance

After configuration is acknowledged, the private journal drops bearer tokens and
retains public grant receipts. Platform startup and successful dependency starts
activate one periodic owner task, independently of GUI windows or Agent Runs.
It observes exact consumer state, renews grants with at most five minutes left,
and revokes recorded grants when an authoritative snapshot shows a stopped,
removed or replaced consumer. Node query failures and foreign-owner snapshots
are deferred, not interpreted as termination. Each grant remains bounded to a
15-minute expiry; the task checks again after each pass with a 30-second interval.

The same per-attempt lock coordinates initial starts and maintenance. Restarting
the platform on the same private data root resumes existing receipts without
reconfiguring, reissuing grants or replaying tool calls. A lost renewal response
can leave the local expiry stale: maintenance retries the same grant ID, and only
the authority's 410 response proves that it cannot be renewed. Stopping the
platform does not revoke live Apps' grants; they remain subject to their TTL.

This is not yet a production-complete dependency lifecycle. Old start journals
without receipts are not adopted automatically. Gateway restart loses grants;
durable gateway authority, resource sessions, distributed owner fencing, and
live fleet deployment acceptance remain unfinished. Invalid/expired maintenance
records are summarized to platform diagnostics without private error content.

## Provider resource-session contract

Go providers can now use `fleet/appsvc.ManagedHost` and `ManagedCommand` for the
same ordinary `backend/http` invocation path as portable Apps. The host accepts
`{method,args,timeout_s}` on authenticated loopback `/rpc` and returns
`{success,result}`. Request/result size is bounded to 512 KiB, actual work is
concurrency-limited, and timeout/disconnect does not release its drain lifetime
before the handler and result serialization finish. This is a local provider
host, not a replacement for the scoped dependency gateway.

`fleet.json` running-component hooks now support native process components as
well as containers. Native hooks receive the Runner-owned identity, RPC token
and assigned ports through their environment, never in the public ledger or
hook input. `/health` pins the identity before a stop probe calls
`/_fleet/drain`; no mutable data-directory endpoint file chooses the destination.
Only declared completion methods remain available during drain; new work is
rejected, and actual cleanup must succeed before `safe_to_stop` becomes true.

Shell has an opt-in native package builder using this host. Its legacy builtin
manifest remains unchanged. See `apps/shell/README.md` for packaging and the
remaining owner/session/workspace integration. A real native Fleet lifecycle
test covers the generated executable rather than a stand-in provider. This
does not yet prove Agent-scoped grant assembly against the managed Shell or
deployment on a remote HPC job node.

`resource-session@1` is a separate optional App interface for owner-controlled
ephemeral resource leases. Its methods are `resource_session_acquire`,
`resource_session_get`, `resource_session_renew` and `resource_session_release`.
All require an opaque `owner_ref` (1–100 letters, digits, underscore or hyphen)
and a stable `lease_id` (64 lowercase hex characters). Acquire additionally
requires `kind`; acquire/renew accept integer `ttl_seconds` in 30–900, default 900.

The receipt fields are `lease_id`, `owner_ref`, `kind`, `session_id`, `state` and
Unix-second `expires`. States are `active`, `closing`, `released`, `expired`,
`lost` or `failed`. Acquire reserves the request ID before resource creation;
retrying an acknowledged or unknown acquisition cannot create a second resource.
Changed owner/kind is rejected. Retry does not extend expiry: renewal is explicit,
cannot shorten expiry, and never resurrects a non-active resource. Close failure
stays `closing` and remains eligible for provider cleanup rather than reporting
success. No raw creation/cleanup errors are returned in receipts.

The Go App SDK implements this as `appsvc.SessionRegistry` and manifest-bound
`SessionHandlers`. Providers supply local resource creation, liveness and bounded
cleanup callbacks; the framework has no Agent-specific key or Shell branch.
Borrowed durable resources must implement cleanup as releasing an attachment,
not deleting the user's underlying resource. Shell is the first provider and
currently supports `kind: shell`. It binds normal command admission to lease
state and returns its existing shell ID for use as a grant-bound `shell_id`.

IDs and leases are not authorization. These management methods are for the
owner-authenticated control plane; ordinary tool consumers should receive only
their required methods with bound resource arguments. Shell's existing owner
NATS service is the current transport. Automatic platform acquisition/renewal,
durable coordinator receipts, scoped consumer assembly and cross-generation
recovery are still required; the Go Shell's builtin transport is not yet a
standalone managed HTTP App deployment. Do not treat this interface as completion
of the resource-session migration.

Receipts, including terminal tombstones, persist only for the provider process
lifetime. The registry caps them at 4,096 and rejects further new acquisitions
rather than forgetting an old ID and replaying it. This is fail-closed bounded
storage, not a finished durable garbage-collection policy. Provider restart must
be fenced by the same generation-bound transport as other App calls. The SDK
periodically sweeps leases; tool admission also checks actual expiry, so a slow
cleanup does not authorize a new command on an expired managed Shell. Shell
release verifies its root process exit; full detached process-tree ownership is
not established by these session tests and remains a lifecycle acceptance item.
