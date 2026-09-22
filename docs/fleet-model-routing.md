# Fleet model route selection

An alias resolves an authorized set of exact deployments and models. Each call
freezes the selected instance generation and connector configuration. It does
not migrate an active stream when the alias, node or deployment changes.

`ordered` preserves the user's candidate order. `ready_first` compares:

1. Whether the model is already loaded.
2. Live occupancy: active plus queued calls divided by capacity.
3. Measured cold-load duration, for otherwise tied unloaded candidates.
4. Candidate order for ties.

A cold-load measurement is the median of up to 16 confirmed cold loads during
the past seven days on that deployment, for the same imported model and exact
connector configuration. The configuration pins the engine recipe, context,
parallelism and resource reservation. Changing configuration invalidates the
previous estimate. Loaded no-op operations, unloading, failed or uncertain
operations and ordinary inference latency are not load measurements. The node
persists only the bounded numeric samples, not prompts or generated content.

Unknown, stale or malformed measurements are not treated as zero cost. After
loaded state and occupancy tie, measured durations rank before unknown ones.
Selection metadata includes the measurement, sample count, observation time,
loaded state and occupancy so consumers can explain the choice. These are
observations and preferences, not a latency guarantee. This is not a weighted
prediction of queue wait or end-to-end inference time.

Connector preflight is read-only; it never downloads, loads or generates just
to rank a candidate. An explicit `inference_ready: false` excludes a model even
if it has a past load measurement. For example, the current managed llmster
driver requires loading its exact instance through owner model controls after
its TTL expires. Managed Ollama may load a validated imported model when an
authorized inference request arrives. SGLang must already advertise its pinned
resident model. Attached and older connectors without measurements remain
compatible; no management authority is implied by a workload grant.

`fallback=none` still restricts selection to the first candidate. Other allowed
candidates are considered only before submission. An interrupted or uncertain
generation is not replayed on another model. Platform-budget fallback is not
part of a Fleet alias; billing and data-location permissions remain explicit.
