# Real Desktop app acceptance

These scripts exercise the real path from DesktopToolSet through
AppInstanceResolver, Go fleet, NATS, the UI bridge and the rendered application.
Only the browser fixture's network adapter is replaced; app implementations,
backend results and screenshots are real. The core and scientific suites invoke agent tools directly. The optional LLM
suite also exercises a real Pantheon Agent choosing its tools.

Use a dedicated scratch directory and a separate browser viewport. The runner
closes windows in that scratch workspace at startup. Run suites sequentially
when sharing one server; use different roots and API ports for parallel suites.
Nothing connects to a Hub account or an existing user workspace.

## Prerequisites

- A working runtime development Python environment, including `aiohttp` and
  `nats-py`. Launch `server.py` with the interpreter to use for local app
  backends. Named environments configured for AppSupervisor may take precedence.
- Scientific backend packages in that environment: `numpy`, `pandas`, `anndata`,
  `zarr`, `tifffile`, and `umap-learn` for UMAP calculation. The fixture generator
  directly imports `numpy`, `pandas`, `anndata` and `tifffile`.
- Go and `nats-server`, or already built fleet and NATS executables.
- The sibling `pantheon-ui-apps` checkout with its Node dependencies installed,
  including Playwright, and its Vite development server running.
- Chrome or Playwright Chromium. On macOS the runner uses installed Google
  Chrome by default; elsewhere install Playwright Chromium or set
  `CHROME_EXECUTABLE`.
- Internet access for the viewers' existing CDN dependencies. Fixture generation
  downloads only two small public PDB files from RCSB; all other input data is
  generated locally. Large scientific datasets are not vendored.

## Start an isolated server

Set paths for your machine. `AUDIT_PYTHON` should be an already provisioned
runtime/science environment; these commands do not alter the repository's venv.

```sh
export RUNTIME_REPO=/path/to/PantheonOS-refactor
export UI_REPO=/path/to/pantheon-ui-apps
export AUDIT_ROOT=/tmp/pantheon-desktop-audit
export AUDIT_PYTHON=/path/to/science-venv/bin/python
export AUDIT_API=http://127.0.0.1:48180
export UI_URL=http://localhost:5173

mkdir -p "$AUDIT_ROOT"
go -C "$RUNTIME_REPO/fleet" build -o "$AUDIT_ROOT/fleet" ./cmd/fleet
"$AUDIT_PYTHON" "$RUNTIME_REPO/scripts/desktop_audit/generate_scientific.py" --root "$AUDIT_ROOT"
```

Run Vite in a separate terminal if it is not already running:

```sh
npm --prefix "$UI_REPO" run dev -- --host localhost
```

Start the adapter in the foreground, providing your actual NATS binary path:

```sh
"$AUDIT_PYTHON" "$RUNTIME_REPO/scripts/desktop_audit/server.py" \
  --root "$AUDIT_ROOT" --port 48180 --ui-url "$UI_URL" \
  --fleet-bin "$AUDIT_ROOT/fleet" --nats-bin /path/to/nats-server
```

Wait for `AUDIT_RPC_READY`. The server writes `runtime.json`, `nats.log` and
`fleet.log` below the scratch root. It binds HTTP and NATS only to loopback,
uses a fresh fleet namespace/state directory for each run, and mounts
`$AUDIT_ROOT/workspace` as its workspace. `--runtime` can point to another
runtime checkout; the default is the checkout containing the script.

Core/negative/LLM suites may reuse the same adapter:

- `POST /rpc`: `{ "method": "desktop_windows", "args": {}, "toolset": "desktop" }`.
  Existing `method_name`/`toolset_name` aliases are also accepted.
- `GET /events`: SSE containing `{ "stream": "...", "message": ... }` from NATS.
- `GET /meta`: `{ "audit": true, "root": "...", "workspace": "...", "tag": "..." }`.

The shared UI fixture is
`pantheon-ui-apps/scripts/desktop/fixtures/all-apps-live.ts`. Its `api` URL query
parameter selects the adapter, defaulting to `http://127.0.0.1:48180`. There is
no separate `science-live.ts` and no file to copy out of `/tmp`.

## Run scientific controls

In another terminal with the same environment variables:

```sh
node "$RUNTIME_REPO/scripts/desktop_audit/scientific.mjs"
```

Configuration:

| Variable | Meaning |
| --- | --- |
| `AUDIT_API` | Shared server URL; default `http://127.0.0.1:48180` |
| `UI_URL` | Vite URL; default `http://localhost:5173` |
| `UI_REPO` | UI checkout used to import Playwright |
| `RUNTIME_REPO` | Runtime checkout; defaults to this script's checkout |
| `AUDIT_OUTPUT` | Output directory; defaults to `<server root>/scientific-results` |
| `CHROME_EXECUTABLE` | Optional browser executable |
| `HEADLESS=0` | Show the test browser |
| `ONLY=cytoscape,igv` | Run a selected subset |

For a small smoke test without molecular downloads, generate fixtures with
`--skip-molecules` and run `ONLY=cytoscape,igv`. The main suite uses a synthetic
local FASTA reference for IGV, avoiding dependence on a large external genome.

| App | Representative control |
| --- | --- |
| Cytoscape | Network layout → circle |
| PhyloTree | Linear tree → radial |
| MSA | Hide sequence ruler |
| RDKit | Show atom indices |
| Mol* | Protein → DNA structure |
| Gosling | Genomic track color |
| IGV | Navigate a local reference locus |
| Spatial3D | 3D cluster → 2D gene coloring |
| Volume3D | ISO → MIP and brightness |
| Vitessce | Toggle heatmap panel |
| Viv | Two channels → red channel only |

For each app the runner opens a window, reads it, opens a second same-app
window, controls the original, checks the second state is unchanged, compares
rendered app pixels (excluding window chrome), saves a real
`desktop_screenshot`, sends invalid input and requires failure, then restores
the same window. The process exits nonzero on failure. Result JSON is written
after each app, including exact responses and window IDs. Evidence PNG/JPEGs
are below `scientific-results/evidence`.

Inspect before/after and tool screenshots in addition to the assertions.
Changed pixels alone cannot establish that a scientific visualization is
semantically correct. In particular, check that WebGL plots appear in the
tool screenshot rather than just in the browser screenshot. This suite covers
representative small inputs, not every format, parameter or remote dataset.

Press Ctrl-C in the **server** terminal when finished. It shuts down its own
fleet/NATS process groups and writes `stopped.json` and `rpc-summary.json`.
Keep scratch data for debugging or remove that chosen scratch directory later.

## Fast regression checks

```sh
cd "$RUNTIME_REPO"
node --experimental-vm-modules --test apps/notebook/frontend/control.test.mjs
"$AUDIT_PYTHON" -m pytest tests/test_scientific_app_backends.py \
  tests/test_desktop_capability_discovery.py -q
```

The Notebook test loads the actual controller unchanged, substituting only the
large Vue renderer dependency. It reproduces delayed transport responses:
a poll started before an edit cannot complete that edit, and an older response
arriving after a fresh poll cannot overwrite the visible document.

## Core controls and failure recovery

With the isolated server running, generate only small local inputs and run:

```sh
"$AUDIT_PYTHON" "$RUNTIME_REPO/scripts/desktop_audit/generate_core.py" --root "$AUDIT_ROOT"
node "$RUNTIME_REPO/scripts/desktop_audit/core.mjs"
```

The 66 assertions cover Files, Terminal, Image/Text/PDF viewers, Notebook,
bespoke `agent-view`, and the explicitly unsupported content interfaces of
Agent/Settings/Store/Interfaces. This requires `ipykernel` for real Notebook
execution. It checks saved bytes, same-PTY output and interrupt, real PDF page
text, Notebook output in the existing UI, invalid input/recovery, cancellation,
a deletion confirmation followed by navigation, a second untouched window,
and real tool screenshots. Reports go to `<server root>/core-results`.

Browser and QuPath need a Linux display/Xpra runtime and use the corresponding
`tests/test_browser_*`, `tests/test_native_*`, `tests/test_qupath_*` suites plus
live GUI acceptance. The local NATS adapter does not emulate Linux native apps.
ImageJ can use the same server; run the UI checkout's
`scripts/desktop/check-imagej-file-control.mjs` for the real ImJoy import,
macro and screenshot path. The independent UI
`check-imagej-control.mjs` and `check-bridge-contract.mjs` cover cold boot,
image identity guards and asynchronous bridge behavior.

## Optional real-model acceptance

Use the configured provider's environment variables; no credential is stored
in these scripts. For an OpenAI-compatible proxy use `OPENAI_API_BASE`,
`OPENAI_API_KEY`, and the appropriate `AUDIT_MODEL` (including the provider
prefix expected by Pantheon). This makes real, billable model requests.

```sh
export AUDIT_OUTPUT="$AUDIT_ROOT/llm-results"
export AUDIT_MODEL=provider/model
node "$RUNTIME_REPO/scripts/desktop_audit/llm-viewport.mjs"
```

Once it prints `LLM_VIEWPORT_READY`, in another terminal with the same variables:

```sh
"$AUDIT_PYTHON" "$RUNTIME_REPO/scripts/desktop_audit/llm.py"
```

The model gets only `desktop_windows`, `desktop_read`, and `desktop_call`.
It must run a command in the specified existing Terminal, edit/save/read the
specified Text Viewer, and navigate/read PDF page 2. The viewport independently
checks the real PTY buffer, saved file bytes, current PDF page, screenshots,
and an untouched second editor. Model/provider failures are separate from
application failures. Keep the model transcript and `llm-acceptance.json`
together. Do not run another suite on this viewport until it completes.
