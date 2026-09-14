# Model request recovery

The agent bounds each provider call with two deadlines, configured in the
`llm_retry` section of settings:

```yaml
llm_retry:
  idle_timeout: 120
  request_timeout: 600
  timeout_cooldown: 600
```

Both values are positive seconds. The idle deadline starts when the provider
call starts and resets only for text, reasoning, or tool-call output. SSE
heartbeats, empty/role-only deltas, and usage records do not reset it. The total
deadline also bounds a provider that emits occasional tokens for many minutes.
These limits cover the provider call, not tool execution or context preparation.

On either deadline, the in-flight provider task is cancelled. The agent tries
the next distinct configured model without retrying the exhausted model first.
For the next ten minutes of this run, working alternatives are preferred over
timed-out models, so every tool round does not pay the same timeout again.
If the fallback chain is exhausted, the normal chat error/completion path runs.
An explicit stop also cancels the in-flight provider task.

For OpenRouter, catalog output ceilings describe capability, not the desired
size of every answer. Requests default to at most 32,000 output tokens. Explicit
output limits remain supported; they are bounded by the routed provider's
context window after accounting for the input, tool definitions, and estimation
headroom. The smaller of the model and top provider context windows is used.

Changing these settings/code does not alter an already-running request. Deploy
the runtime and restart an idle agent worker to apply the change.
