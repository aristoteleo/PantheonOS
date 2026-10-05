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

## Independent lifecycle preparation

An independent host can give `EvolutionToolSet` an explicit work directory and
`EvolutionManager(workdir)` instead of the legacy process singleton. Such a
manager restores only its own directory, refuses malformed session documents,
and marks previously active work as failed after process loss without replaying
it. Existing checkpoints and source artifacts remain available for inspection.
Legacy construction retains its original session-manager selection.

Each accepted evolution has one tracked task in both synchronous and background
mode. Expiring the synchronous wait or losing its caller leaves that same task
running. Cancellation waits for execution to settle before acknowledging a
terminal state. App shutdown stops admission, cancels and joins all runs created
by that instance, and requires terminal records to be saved atomically. A save
failure is reported rather than treating shutdown as successfully persisted.
Parallel workers are joined when their collector exits. Evaluation subprocesses
are reaped on completion, cancellation and timeout; POSIX also terminates their
process group. Windows descendant-process acceptance remains outstanding.

These are lifecycle prerequisites, not the finished standalone App package.
The default EvolutionTeam still constructs internal Agent workers; explicit
Agent/model/tool dependencies and full prepared package acceptance remain part
of the Agent extraction plan.
