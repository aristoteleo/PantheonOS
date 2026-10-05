# Ordinary Evolution App

This package preserves the Evolution tool API, background sessions, progress,
cancellation, archive and reports. Reasoning consumes a prepared `agent-execution@1`
dependency; it does not embed an Agent or discover model credentials.

Supply `values.evolution` with `agent_credential: "agent"`, `execution: "node"`
or `"isolated"`, and an explicit `options` object. The corresponding `agent`
credential is an owner-issued RPC dependency with the consumer identity bound
by the grant issuer. The Agent continues to use original Model Services.

Node execution runs code under the node's ordinary native-App trust boundary.
For isolated execution also supply `placement` with `kind: "modal"`, a reviewed
`image` (app_id, version, artifact_sha256, image_id, base_image_id), app_name,
timeout, cpu, memory, gpu (null for CPU), and `credential: "modal"`. The image
must contain the separately prepared `evolution-tools` App. The private Modal
credential has endpoint `https://api.modal.com` and a JSON key containing
`token_id` and `token_secret`. It stays on the controller, never in workloads.
No ambient Modal profile, API key or alternative node is selected.

## Composing with the Agent App

Use the ordinary owner deployment recipe (or the local product's `providers`
setup), with an `evolution` provider and its `agent` startup binding:

```python
from pantheon.apps.agent_execution_client import execution_method_rules

binding = {
    'app_id': 'agent',
    'component': 'backend',
    'provider': {'$app': 'agent', 'component': 'backend', 'port': 'http'},
    'methods': execution_method_rules('evolution-controller'),
}
```

The owner chooses a durable consumer identity unique within the Agent deployment;
`evolution-controller` is an example for one controller. Reuse that identity for
the same controller's recovery, never across unrelated controllers. The gateway
binds it on all execution methods, so Evolution cannot choose another caller's
execution namespace. The binding alias must be `agent`, matching the package's
credential declaration. The owner installs/configures the exact packages before
starting this consumer; the App never requests its own management credential.

In the Agent release, declare the additional `evolution@1` dependency with
`binding: "runtime"` and a compatible version range. In the owner setup, add its
approved public function schemas to Agent's `dependencies.profiles.toolsets`,
and the corresponding provider/method policy to the allocator's `tools`. These
are ordinary runtime tool grants, not another startup binding. The resulting
order is model access/allocator, Agent, then Evolution. Conversation tool
allocation begins after the composition is ready. Synchronous Evolution calls
can then invoke the same Agent's execution service while its chat awaits the
tool result. Do not put a startup binding to Evolution on that Agent: that would
create a real dependency cycle.

For a private local Fleet profile, `agent_ca_pem: {"$local": "trust_roots_pem"}`
in `values.evolution` supplies the owner's loopback TLS trust. Omit it for normal
publicly trusted endpoints. The `node` execution option runs tools in Evolution's
own process tree; the Agent only performs inference through its existing Model
Services grant. A shared Evolution service retains its own sessions rather than
reusing an Agent's Shell session.

Workspace and private state must be separate directories. Sessions, archives
and recovery receipts live in private state; codebase input and exported outputs
are restricted to the prepared workspace. Shutdown stops admission, joins runs,
confirms owned placements have stopped, then closes dependency clients. An
uncertain result retains recovery evidence and is never automatically replayed.

`tests/test_local_profile_evolution.py` installs both independent releases with
the native local Controller/Runner and original Model Services Connector. It
exercises the callback path using real owner-issued grants and actual Shell,
Python and evaluator work, followed by ordered profile stop and reopen. The
upstream model replies are scripted. This candidate is not the default product
composition: full General Team, tool sampling/image bindings, automatic whole-run
reconciliation and deployed cross-node acceptance remain separate gates.
