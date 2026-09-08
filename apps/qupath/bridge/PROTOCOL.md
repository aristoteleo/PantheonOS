# QuPath GUI bridge protocol v1

The desktop process manager creates a `QuPathBridge` per native session and
passes its three environment variables and `startup_option()` to the child
JVM. QuPath 0.7.0 itself evaluates `startup.groovy` in the GUI through its
documented `qupath.startup.script` property. No network port, extension JAR, or
headless QuPath instance is used. The bridge is available only after
`ready.json` appears; it identifies the JVM PID, session, protocol, and QuPath
version. The process manager must also check that this session's process is
still alive. A ready file does not independently prove liveness.

The 0700 directory and 0600 request files are private to the sandbox user.
Every request additionally carries the native session ID and a random token
provided through the child environment. Neither the token nor the private
directory is part of the public desktop result. This is a trusted automation
interface with the permissions of QuPath; Groovy is not a security sandbox.

`QuPathBridge.call(method, params, request_id=..., timeout=...)` atomically
publishes immutable JSON, then waits up to the caller's budget. The GUI's
single daemon worker processes requests in file creation order. Responses
are atomically replaced and carry `session_id`, `request_id`, and `state`:

- `queued`: published but not yet started (synthesized by the Python client).
- `running`: execution has been claimed; the record is written before work.
- `succeeded`: includes `result`, `stdout`, `stderr`, and `output_truncated`.
- `failed`: includes `error` and, after execution starts, captured output.
- `expired`: the queue deadline elapsed before work started; no work ran.
- `unknown`: that ID was never submitted to this native session.

Started and completed responses include `started_at` and `finished_at` Unix
timestamps as applicable. Reusing an ID with the same method and parameters
returns the existing request; changing the parameters raises an error. The
request and response remain for the lifetime of the session. If the JVM dies
after writing `running`, that outcome is indeterminate and must not be
automatically replayed in a new session.

The Python timeout stops waiting; it does not kill, interrupt, roll back, or
resubmit a running Groovy script. Its result has `wait_timed_out: true` and the
original ID. Query `request_status(id)` to recover its outcome. This reads a
local response file and remains responsive even if JavaFX or analysis is
busy. Exceptions may occur after partial mutations or file writes; they are
not transactions. Scripts should explicitly verify their intended results.

## Methods

`state` accepts `annotation_limit` (default 200, 0–2000). Its result contains
current GUI `image`, `project`, and `viewer` state; image dirty status;
annotation/detection/selection counts; selected object IDs; and a bounded
list of annotation IDs, names, classes, locks, and ROI bounds. ROI and viewer
coordinates are full-image pixels, independent of Xpra screenshot scaling.
Missing image/project values are null. State reads run on JavaFX, so a busy
GUI may leave them pending; polling their ID never waits on JavaFX.

`script` accepts `script` (Groovy source), `args` (string array), and `thread`
(`worker`, default, or `fx`). The bridge uses QuPath's own `GroovyLanguage`
and `ScriptParameters`, with the current GUI image/project captured on
JavaFX, the usual QPEx static imports, and QuPath core classes. `args` is the
normal QuPath script argument binding. Each call gets fresh bindings.
Long analysis belongs on the worker. JavaFX scene/view mutations belong in
a short `fx` call, or an explicit `Platform.runLater` in the script; an
asynchronously scheduled closure can finish after the script returns. The
bridge dispatches final hierarchy notifications to JavaFX. User interactions
can still change the active view during a worker analysis; the captured
ImageData remains that script's analysis target.

Scripts return JSON-compatible maps with string keys, collections, strings,
finite numbers, booleans, or null. Returning QuPath/JavaFX objects is an error;
return IDs or summaries and export large scientific results to files. Limits:
600 KB per request, 1 MB per response, 64 KB each for stdout/stderr, 10,000
result values, nesting depth 12, and 256 KB per result string. QuPath's
SLF4J/application logs remain in its log file; stdout/stderr here are the
script engine writers, not global process stream interception.

## Official source references

- [GUI startup and runScript](https://github.com/qupath/qupath/blob/v0.7.0/qupath-gui-fx/src/main/java/qupath/lib/gui/QuPathGUI.java)
- [ScriptParameters](https://github.com/qupath/qupath/blob/v0.7.0/qupath-core-processing/src/main/java/qupath/lib/scripting/ScriptParameters.java)
- [QuPath's Groovy execution and bindings](https://github.com/qupath/qupath/blob/v0.7.0/qupath-gui-fx/src/main/java/qupath/lib/gui/scripting/languages/DefaultScriptLanguage.java)
