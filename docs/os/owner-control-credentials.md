# Durable credentials for owner-control Apps

The dependency allocator and Model Services access App act for the Fleet owner.
They need a credential that survives a browser logout or the twelve-hour Fleet
session expiry. Use a dedicated, revocable **existing platform API key** (`pbk_`)
for these trusted Apps. Do not put that key in Agent configuration. Agent keeps
its consumer-bound model/tool grants; upstream model keys remain with Connectors.

The paired Hub supports platform keys only on explicit Fleet workload routes:
Model Services control, dependency grants, workload connection and startup reads.
Key creation/revocation, startup edits, budget-key retrieval, browser connection
and login/admin APIs still require their original owner login. Key validation
queries the existing database on every request, without caching authorization or
minting a long-lived JWT. This does not create a second key database.

Create a dedicated key using the existing authenticated platform-key UI/API and
save its one-time value to an absolute, owner-private file (mode 0600). Then run
from the owner provisioning environment:

```sh
python -m pantheon.platform.owner_credentials \
  --hub https://YOUR_PAIRED_HUB \
  --key-file /private/path/control-platform-key \
  --owner YOUR_FLEET_OWNER \
  --node-id TRUSTED_CONTROL_NODE \
  --ref-prefix owner-control-v1 \
  --output /private/path/new-control-references.json
```

Repeat `--node-id` only for other nodes hosting trusted owner-control Apps. The
command confirms the key's owner and controller at the paired Hub's
`/api/fleet/apps/workload-identity`, joins that controller, checks every selected
node's identity/import protocol, then delivers endpoint-bound credentials using
Fleet's existing encrypted vault import. No plaintext enters node RPC records or
the output descriptor. The helper is not packaged in the Agent App.

The result maps `nodes[NODE].hub` and `.controller` to ordinary `{ref, endpoint}`
values accepted by the existing deployment composer. Supply both to the
allocator; supply only `hub` to model-access. Keep Agent's owner credential map
empty: the generic deployment machinery supplies its narrowed dependencies.
No App is installed, started, stopped or rotated by this command.

A reply can be lost after a node saved a value. Retry with the **same key, nodes
and reference prefix**, choosing a new output file if needed. Matching values
are idempotent; conflicts never replace existing credentials. If provisioning is
partial, already delivered entries stay in their vaults. To rotate a key, mint a
new dedicated key, deliver it under a new prefix, explicitly replace the trusted
control App generations with the reviewed references, verify them, then revoke
the old key using the existing key-management API. No automatic disruptive
restart is attempted.

Revocation blocks new Hub calls and grant renewals. Previously issued data grants
can remain valid until their bounded expiry, and existing NATS connections have
their own credential lifetime. Immediate teardown is a separate explicit App/
grant lifecycle operation; revocation is not a claim that every live connection
has already closed.

This is an owner provisioning path, not the complete first-run UI or production
cutover. It does not import old credentials, change model selections, deploy to a
live Fleet, or replace the legacy CLI/Desktop login flow.
