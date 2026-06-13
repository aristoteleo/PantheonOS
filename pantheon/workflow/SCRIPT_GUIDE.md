# Workflow Script Guide (Leader)

You are the **Leader**. To run a multi-step job you write a *workflow script* and
pass it to `workflow_create(goal, script, args=...)`. This guide is the contract
for writing scripts that the engine will accept and run correctly.

---

## 1. What a workflow script is

A workflow script is a **restricted async Python program** that you author. The
engine validates it, then runs it in a sandbox. Inside the script you call a
small orchestration API; each `node(...)` call dispatches **one focused subagent
task**. Node results flow through **files on disk**, not back into your context —
you stay lightweight and read results later via `workflow_get_output`.

Key properties:

- The script body may use `await`, `for`/`if`/`while`, comprehensions, inner
  `def`/`lambda`, and a top-level `return` for the final value.
- It runs as the body of a generated `async def`, so `await` and `return` at the
  top level are legal.
- It is **deterministic** (see §4) — this is what makes cheap *resume* work: an
  unchanged prefix of nodes is served from a journal cache and never re-run.

---

## 2. The `meta` literal

Begin the script with a `meta` assignment whose value is a **pure literal dict**:

```python
meta = {"goal": "Audit the codebase for security issues", "phases": ["scan", "review", "report"]}
```

- The engine **AST-extracts** `meta["phases"]` *before* execution to build the
  plan skeleton it shows the user, so `meta` must be a literal — no names, calls,
  or f-strings inside it. A non-literal `meta` (or a missing one) yields empty
  phases (no error), but you lose the pre-execution plan.
- `phases` is a list of strings. Use the same strings later in `phase("...")`.
- `goal` here is informational; the authoritative goal is the `goal` argument you
  pass to `workflow_create`.

---

## 3. The primitives

Five names are injected into the script: `node`, `parallel`, `pipeline`,
`phase`, `log`. Plus an `args` global (see §4).

### `node(...)` — dispatch one subagent task

```python
result = await node(
    instruction,            # str: the task for the subagent (required, positional)
    template="generic",     # str: node-agent role template (Phase 1: only "generic")
    schema=None,            # dict | None: JSON schema -> node must return conforming JSON
    inputs=(),             # tuple[str, ...]: relative paths under context/ this node depends on
    label="",              # str: DISPLAY ONLY (may repeat or be empty; never used for addressing)
    phase="",              # str: progress grouping (defaults to the current phase())
    model=None,            # str | None: override the node's model
    timeout=None,          # float | None: per-node wall-clock seconds
)
```

You **must `await`** the call. It returns:

- the **validated dict** if `schema` was given (the node's JSON output), else
- the node's **text** result, else
- **`None`** if that node was *skipped* (resume / `skip_node`).

Parameter meanings:

- **`instruction`** — the natural-language task. This is the subagent's user
  message. It also participates in the cache key, so editing it re-runs that node.
- **`schema`** — a JSON schema. When set, the node agent's final reply MUST be
  valid JSON conforming to it; the runner validates and **retries once** on a bad
  attempt, then fails the node. The schema is part of the cache key.
- **`inputs`** — relative path *segments* under the workflow's `context/`
  directory (typically an earlier node's `n{id}.json`). Each declared input
  file's **content is hashed into this node's cache key**, so if an upstream
  output changes, this node is invalidated and re-runs on resume. Declaring
  inputs is how you make resume-invalidation track real data dependencies.
  Path segments must be plain (no `/`, `..`, or absolute paths).
- **`label`** — display only. It shows up in `workflow_status` and events. It may
  be empty or repeat. It is **never** used to address a node or build a filename.
  Nodes are addressed by their integer `node_id` (assigned in source order).
- **`phase`** — overrides the grouping for this node; defaults to whatever the
  last `phase("...")` set.
- **`model`** / **`timeout`** — optional per-node overrides.

A node that finishes with status `failed` raises a `NodeError` (carrying
`node_id`, `label`, `error`). An unhandled `NodeError` fails the whole workflow.

### `parallel(thunks)` — concurrent fan-out

```python
results = await parallel([
    lambda: node("review auth.py", label="auth"),
    lambda: node("review db.py", label="db"),
])
```

- `thunks` is a list of **zero-argument callables**, each returning a `node(...)`
  awaitable (a `lambda: node(...)`). They run concurrently.
- Returns a **list aligned to the thunk order**, holding each result **or its
  exception**. A failed node yields a `NodeError` *in the list* — siblings are
  **not** cancelled.
- Collect successes by filtering exceptions:

  ```python
  results = await parallel([...])
  ok = [r for r in results if not isinstance(r, Exception)]
  ```

Node ids are allocated in **source order, depth-first**, at call time — so the
fan-out gets ascending ids regardless of which node finishes first. (You may nest
`parallel` inside a thunk; the nested block returns a sublist.)

### `pipeline(items, *stages)` — per-item staged chains

```python
results = await pipeline(
    candidates,
    lambda prev, item, idx: node(f"verify {item}", label=item),   # stage 1
    lambda prev, item, idx: node(f"summarize {prev}", label=item), # stage 2
)
```

- Each `item` flows through the stages as an **independent chain** with **no
  barrier** between stages (item A can be in stage 2 while item B is still in
  stage 1).
- Each stage is a callable `(prev, item, index)` returning an awaitable; `prev`
  is the previous stage's result (the `item` itself for stage 1). A `(prev,)`-only
  stage is also accepted.
- Returns a list aligned to `items` with each item's **last stage** result, or an
  exception if any of its stages raised (other items unaffected).
- **Phase-1 constraint: exactly one `node()` per stage.** A stage that calls
  `node()` more than once breaks deterministic id allocation for the extra calls
  (the engine logs a warning). One node per stage — always.

### `phase(title)` and `log(message)` — progress (synchronous)

```python
phase("scan")          # set the current phase; emits phase_changed
log("starting fan-out") # free-form progress line
```

Both are **synchronous** — call them WITHOUT `await`. `phase()` sets the phase
used by subsequent nodes that don't pass an explicit `phase=`.

---

## 4. Determinism rules (HARD)

The sandbox gives the script a **curated, locked-down namespace**. The following
are **not in scope and raise `NameError`** (some are statically rejected at
validation time, failing the script before it runs):

- **No imports** — `import x` / `from x import y` are rejected.
- **No `time`, `random`, `datetime`** — non-determinism breakers.
- **No `open`, `os`, `sys`, `eval`, `exec`, `compile`, `getattr`/`setattr`,
  `globals`/`locals`**, no dunder attribute access (`x.__class__`, etc.).
- File and network access must go through the injected `node` API — there is no
  direct IO.

Available builtins are pure helpers only: `len, range, enumerate, zip, map,
filter, sorted, reversed, min, max, sum, abs, round, pow, divmod, all, any,
list, dict, set, frozenset, tuple, str, int, float, bool, bytes, complex,
isinstance, issubclass` and the common exception types. `print` exists but is a
no-op (use `log(...)`).

**If you need a timestamp, seed, or any external value, pass it through `args`.**
The script receives whatever you passed as `workflow_create(..., args=...)` as a
global named `args`:

```python
seed = args["seed"]            # args came from workflow_create(args={"seed": 7})
```

This determinism is exactly what lets resume reuse an unchanged prefix.

---

## 5. The skip idiom

A skipped node returns `None`. When you collect results that may include skips,
filter `None` out:

```python
results = await parallel([lambda m=m: node(f"check {m}", label=m) for m in modules])
done = [r for r in results if r is not None]   # drop skipped nodes
# or, when results cannot be exceptions:
done = list(filter(None, results))
```

(Remember `parallel` results may *also* contain `NodeError` exceptions — filter
those separately if you did not let a failure abort the script.)

---

## 6. Output contract for node agents

You do **not** write a node's output format — the engine's protocol layer tells
each subagent how to reply:

- Every node writes its output to its own `context/n{id}.json` file (a
  `{"kind", "result"}` envelope). You read it back later via
  `workflow_get_output(workflow_id, node_id=...)`.
- If you pass a `schema`, the node's final reply MUST be valid JSON conforming to
  it. The runner validates and retries once; persistent non-conformance fails the
  node. So **schema nodes give you structured, validated dicts** — prefer a schema
  whenever a downstream node or your own logic needs structured fields.

---

## 7. Worked examples

### (a) Dynamic fan-out from a prior node's output

```python
meta = {"goal": "Review changed modules", "phases": ["discover", "review"]}

phase("discover")
modules = await node(
    "List the changed module file paths as a JSON array of strings.",
    schema={"type": "array", "items": {"type": "string"}},
    label="discover",
)

phase("review")
reviews = await parallel([
    lambda m=m: node(f"Review module {m} for bugs.", label=m, inputs=("n0.json",))
    for m in modules
])
return [r for r in reviews if not isinstance(r, Exception)]
```

**Why `lambda m=m:`** — the default-argument **captures the current value of `m`**
at lambda-creation time. A bare `lambda: node(f"... {m}")` would close over the
loop variable `m` and every thunk would see its *final* value. Always bind loop
variables with a default arg (`m=m`) inside `parallel`/`pipeline` lambdas.

### (b) Pipeline: discover → verify

```python
meta = {"goal": "Verify endpoints", "phases": ["verify"]}

phase("verify")
endpoints = ["/login", "/logout", "/profile"]
results = await pipeline(
    endpoints,
    lambda prev, item, idx: node(f"Probe endpoint {item}; report status.", label=item),
    lambda prev, item, idx: node(f"Given probe result {prev}, classify risk for {item}.", label=item),
)
return list(results)
```

One `node()` per stage; each endpoint flows independently with no barrier.

### (c) Simple sequential goal

```python
meta = {"goal": "Draft and refine a summary", "phases": ["draft", "refine"]}

phase("draft")
draft = await node("Write a one-paragraph summary of the attached notes.", label="draft")

phase("refine")
final = await node(
    "Tighten and proofread this draft into a final summary.",
    label="refine",
    inputs=("n0.json",),   # depends on the draft -> re-runs if the draft changes
)
return final
```

---

## 8. What NOT to do

- **No nested subagents inside a node.** A node is one focused task; Phase-1 nodes
  cannot spawn further agents or call `node`/`parallel`/`pipeline` themselves.
  All fan-out lives in YOUR script.
- **No `system_prompt` / `toolsets` / `tools` overrides on `node()`.** They are
  not parameters. The only `node()` knobs are listed in §3 (`template`, `schema`,
  `inputs`, `label`, `phase`, `model`, `timeout`).
- **Never rely on `label` for identity or addressing.** Labels are display-only
  and may repeat or be empty. Nodes are addressed by integer `node_id`.
- **No `time` / `random` / `datetime` / `import` / `open` / `os`.** They raise
  (or fail validation). Pass any external value via `args`.
- **More than one `node()` per pipeline stage** breaks deterministic resume — keep
  it to exactly one.
- **Non-literal `meta`** — keep `meta` a pure literal so phases extract cleanly.
