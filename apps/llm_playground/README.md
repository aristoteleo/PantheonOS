# LLM Playground

The App owns single-call inference, explicit source selection, request status and
cancellation, and temporary media uploads/previews. It imports no Agent runtime,
team, conversation, or memory code. The existing source adapters and multimodal
behavior are retained rather than implementing another inference stack.

The backend is an ordinary registry App, started with:

```sh
python -m pantheon.apphost --app-id llm-playground --workdir /path/to/project
```

The supervisor supplies the same authenticated service-bus environment as other
Apps. Each worker has a fixed project root. Settings and `.env` are read in a
private mapping, without changing another App's environment or Agent routing.
Provider keys and platform virtual keys stay on the backend; catalog results
contain availability and sanitized endpoints, not credentials. OAuth uses the
worker's user credential store. A standalone worker owns and closes its Fleet
model client; the legacy ChatRoom adapter continues borrowing its host client.

Requests and media belong to one running instance. Clients must retain the same
binding for run/status/cancel and upload/read operations. A new process does not
restore temporary media or replay inference. Shutdown rejects new operations,
stops observers, waits for accepted calls, closes owned model connections, and
removes temporary media. Stopping an observer does not claim that a durable Fleet
or upstream video job has stopped; its recorded job reference remains the source
of truth. Graceful cleanup requires the supervisor to deliver a handled stop;
forced process termination cannot execute cleanup.

## Migration status

The frontend still ships through Atrium's `ui:llm-playground` implementation and
uses legacy ChatRoom RPCs. Those six RPCs now inherit the App-owned API. The old
Python module paths are compatibility aliases, not a second implementation.

Do not switch the production frontend yet: generic per-App credential delivery,
runtime budget updates, and pinned client bindings are still required. The
existing Fleet resolver forwards service-bus credentials but does **not** deliver
platform virtual keys, OAuth stores or the Hub/Fleet model credential to arbitrary
nodes. This change does not broaden its environment allowlist. An independently
launched worker needs credentials provisioned in its own authorized environment
or project; local environment injection in a test is not cross-node acceptance.

P2/P4/P6 will provide the generic binding/credential contract and a paired
frontend/backend release. This backend extraction is not that final release.
