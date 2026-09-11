# Evolution

Run and monitor iterative code-improvement experiments with LLM-guided mutations and an evaluation function.

## Using this App

Supply a starting program, an evaluator and the experiment configuration to `evolve`. The evaluator defines what a successful candidate means; inspect its outputs and scoring before starting a longer run.

Use `evolution_manage` to inspect and manage sessions. Keep experiment artifacts in a dedicated workspace directory so candidate code, scores and reports can be traced back to the run. Model availability, evaluation dependencies and compute capacity come from the configured workspace.

## Agent interface

Available tools: `evolution_manage`, `evolve`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
