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
endpoint and access token. Remote delivery/renewal is a separate pending step;
these grants do not authorize writing a remote node's credential vault.

Hub `DELETE /api/fleet/apps/dependency-grants/{grant_id}` is idempotent and limited
to the authenticated owner's Fleet. The Controller endpoints used by Hub are
`POST`/`DELETE /apps/dependencies`, protected by the Controller service credential.
Neither management endpoint is exposed through an App wildcard origin.

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
`preparation_id`, so a prepared consumer cannot call. Startup must finish within
grant validity or the owner must re-authorize it. A stopped, dead or superseded
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
streaming grant, session creation, schema compatibility negotiation, or general
distributed identity proof in this implementation. Bearer holders can act within
their grant while its bound consumer is alive. Native Apps continue sharing their
OS user's trust boundary.
