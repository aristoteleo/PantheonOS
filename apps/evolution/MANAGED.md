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

Workspace and private state must be separate directories. Sessions, archives
and recovery receipts live in private state; codebase input and exported outputs
are restricted to the prepared workspace. Shutdown stops admission, joins runs,
confirms owned placements have stopped, then closes dependency clients. An
uncertain result retains recovery evidence and is never automatically replayed.

This candidate is not the default product composition. Full tool sampling/image
bindings, automatic whole-run reconciliation and real production-grant acceptance
remain separate integration gates.
