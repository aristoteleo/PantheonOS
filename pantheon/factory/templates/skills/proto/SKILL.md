---
id: proto
name: Proto — Generative Biology Design
description: |
  Design biological sequences — DNA, RNA, proteins, ligands, and their
  interactions — by composing generative + predictive AI models on the hosted
  Proto platform (proto.evodesign.org). A Proto program ties generators and
  constraints to sequences; Proto's cloud compiles them into one energy
  function and optimizes to produce designed sequences. The heavy compute
  (AlphaFold, ESM3, Evo, ProteinMPNN, AlphaGenome, 120+ tools) runs remotely —
  no local GPU. Use for de novo proteins/binders, promoters, repressors,
  introns / cell-type-specific splicing, CRISPR systems, signaling pathways,
  etc. — then visualize the designs in Pantheon. Self-contained: drives the
  Proto MCP server over plain HTTP via the bundled proto_client.py.
tags: [proto, generative-biology, protein-design, dna, rna, sequence-design, binder, promoter, intron, crispr, evodesign, mcp]
---

# Proto — Generative Biology Design

Proto is a hosted "programming language for generative biology". You compose a
**program** from four primitives; Proto's cloud runs an optimization and
returns designed sequences. This skill orchestrates that over HTTP and shows
the results in Pantheon — it needs no local GPU and no MCP SDK.

## The model — what you compose

| Primitive | Role | Examples |
|---|---|---|
| **Sequence** `x` | the typed string being designed | DNA / RNA / protein / ligand |
| **Generator** `p(x)` | proposes candidates (the prior) | Evo 2, ESM3, ProteinMPNN, LigandMPNN, uniform mutation |
| **Constraint** `f(x)` | scores a candidate, **lower = better** | AlphaFold/ESMFold pLDDT, AlphaGenome splice usage, GC content, … |
| **Optimizer** | searches sequence space | MCMC / simulated annealing, gradient, rejection sampling, beam search |

A program wires generators + constraints to sequences. Proto compiles all
constraints into a single energy `π(x) ∝ p(x)·exp(−f(x)/T)` and optimizes it.
**Multiple constraints add together** (multi-objective); **multiple generators
factorize** (multi-modal — e.g. design a protein and the DNA it binds at once).
Operationally every campaign is the same loop: *propose → score → keep, repeat
a few thousand steps → return the best sequences.*

Designing well = three decisions: (1) which parts of the sequence are variable
vs. fixed (e.g. fix splice GT…AG, vary the intron core); (2) encode the goal as
a **sum of scoreable constraints** (e.g. "splice in cell A, not cell B" →
maximize SSU in A + minimize SSU in B); (3) pick a generator (strong learned
priors like Evo/ESM converge far faster than random mutation) and an optimizer.

## Setup (one-time)

1. Get a Proto API key: https://proto.evodesign.org → **Settings → API keys**
   (requires a verified email).
2. Put it in the environment — **never hard-code or commit it**:
   ```bash
   export PROTO_API_KEY="sk-..."
   ```
   The endpoint defaults to `https://mcp.evodesign.org/mcp`
   (override with `PROTO_MCP_URL`).

## Driving Proto — discovery-first

Do **not** guess Proto's schemas; discover them live. The bundled
`proto_client.py` exposes every Proto MCP tool through `.call(name, args)`.
The arg names below are **verified against the live server**.

```python
import sys
sys.path.insert(0, "<dir holding proto_client.py>")
from proto_client import ProtoMCP

with ProtoMCP() as proto:                       # reads PROTO_API_KEY
    proto.call("whoami")                        # account + remaining_credits  (CHECK BEFORE SPENDING)
    proto.call("list_tools")                    # → {"result": [ {key, label, category, uses_gpu, hosted, …}, … ]}  (~127 tools)
    proto.call("search_tools", {"query": "splice"})
    proto.call("list_components")               # → {"constraints":[74], "generators":[13], "optimizers":[5]}
    proto.call("get_tool_schema",  {"tool_key": "gc-content"})   # NOTE: arg is tool_key, not name
    proto.call("get_tool_example", {"tool_key": "gc-content"})
```

### Path A — run a single tool (`run_tool`)  ← simplest, verified

For one-off scoring / retrieval / a single model call, skip the full program.
`run_tool` takes `tool_key` + `inputs` (per `get_tool_schema`) and returns a
job result synchronously:

```python
res = proto.call("run_tool", {
    "tool_key": "alphafold-db-fetch",
    "inputs": {"uniprot_id": "P00533"},        # EGFR
})
# → {"job_id","status":"completed","result":{"sequence","entry_id","gene", …}}
```

Many tools are CPU + hosted (39 of ~127): `alphafold-db-fetch`, `blast-search`,
`mafft-align`, `dssp-secondary-structure`, `minced-crispr`, `ensembl-sequence`,
… — cheap. GPU tools (ESM3, Evo, AlphaFold-predict, ProteinMPNN, …) cost more.

### Path B — a full design run (`create_run`)

`create_run` takes `program_data` (the program) + `execute` (bool). The
**verified wire format** (validates `{"valid": true}` against the live server)
is below — note `optimization_stages` (a list of stages; each stage has its own
`generators` / `constraints` / `optimizer`), components referenced by **`key`**
with **`targets`** = segment ids:

```jsonc
{
  "name": "toy", "description": "...", "version": "1.0", "num_results": 1,
  "constructs": [
    { "id": "construct1", "type": "dna",
      "segments": [ { "id": "variable_dna", "label": "100 bp variable DNA", "length": 100 } ] }
  ],
  "optimization_stages": [
    {
      "generators": [
        { "key": "random-nucleotide", "targets": ["variable_dna"],
          "config": { "masking_strategy": {"method":"random","num_mutations":2,"temperature":1},
                      "substitution_scheme": "N" } }
      ],
      "constraints": [
        { "key": "gc-content",     "targets": ["variable_dna"], "config": {"min_gc":40.0,"max_gc":60.0} },
        { "key": "max-homopolymer","targets": ["variable_dna"], "config": {"max_length":5} }
      ],
      "optimizer": { "method": "mcmc",
        "config": {"num_steps":100,"max_temperature":1.0,"min_temperature":0.01,
                   "temperature_schedule":"exponential","proposals_per_result":1,"tracking_interval":1} }
    }
  ]
}
```

Real component keys come from `list_components`: generators `random-nucleotide` /
`random-protein` / `esm2` / `evo1`; constraints `gc-content` / `max-homopolymer` /
`balanced-aa` / `protein-complexity`; optimizers `mcmc` (needs `num_steps`) /
`rejection-sampling` / `beam-search` / `cycling`. For multi-stage designs (e.g.
the promoter→repressor cascade) add more entries to `optimization_stages`.

**Don't hand-guess the schema — start from a worked example.** The proto-language
repo ships ready program JSONs you can copy and adapt (these match the paper's
campaigns):
`https://github.com/evo-design/proto-language/tree/main/examples/jsons` —
`toy.json`, `protein_hunter.json`, `protein_symmetric_homotrimer.json`,
`sigma70_promoter_tuning.json`, `intron_design_splicing.json`,
`evocas9_rejection_sampling.json`, `germinal_pdl1_binder.json`, …
**Always `validate_program` first** (free; returns `{"valid": bool, "message"}`).

```python
proto.call("validate_program", {"program_data": program_data})   # → {"valid": true, ...}
run = proto.call("create_run", {"program_data": program_data, "execute": True})
run_id = run["run_id"]                          # confirm field name from the response
import time
while proto.call("get_run_status", {"run_id": run_id})["status"] not in ("completed","failed","cancelled"):
    time.sleep(5)
proto.call("get_run_metrics",    {"run_id": run_id})   # final scores
proto.call("get_run_timepoints", {"run_id": run_id})   # energy trajectory → plot (L3)
proto.call("fetch_asset",        { ... })              # designed sequences / structures
```

> **Preview-tier limit (important):** a `preview` workspace can run `run_tool`
> (Path A) and Proto's **curated** example programs, but **custom `create_run`
> programs are rejected** ("Preview workspaces can only run curated example
> programs. Request expanded access…") — even though they `validate` fine.
> Authoring + running your own designs needs an expanded-access Proto tier.

### Proto MCP tools

| Group | Tools |
|---|---|
| Discovery | `whoami`, `list_tools`, `search_tools`, `get_tool_schema`, `get_tool_example` |
| Execution | `run_tool` (single tool), `fetch_asset` |
| Design runs | `list_components`, `validate_program`, `create_run` |
| Monitoring | `get_run_status`, `cancel_run`, `run_stage`, `get_run_metrics`, `get_run_timepoints`, `get_run_timepoint` |

## Visualizing results in Pantheon (LiveView)

After a run, show the design to the user with the `live_view` skill (load it for
details). Pairs cleanly with the `structural_biology` skill:

- **3D protein structure** → `molstar`. If `fetch_asset` returns a `.pdb`/`.cif`,
  save it, then:
  ```python
  serve_local_data("design.pdb")                       # → { url }
  open_live_view("molstar", title="Proto design",
                 state={"url": <url>, "format": "pdb"})  # colours by pLDDT
  ```
  No structure file? Predict one from the designed amino-acid sequence with the
  ESMFold API (see `structural_biology`), then show it.
- **Energy / score trajectory** (from `get_run_timepoints`) → a matplotlib line
  plot of energy vs. iteration, or a custom LiveView app for an interactive curve.
- **Designed sequences** → print them; use the `msa` viewer to compare several
  designs or against a natural reference.
- **The program (factor graph)** → optionally render sequences/generators/
  constraints as nodes+edges with the `cytoscape` viewer.

## Notes & guardrails

- Heavy compute runs on Proto's cloud; this skill only orchestrates + visualizes.
- Cost/budget is tied to your Proto account (`whoami` → `remaining_credits`).
  The **preview** tier is metered (observed: ~1 credit even for a CPU
  `run_tool`, cap 10) — **check credits before each run, start with one cheap
  CPU `run_tool`, and scale GPU/`create_run` work deliberately.**
- Designed sequences are **predictions**: in-silico scores (pLDDT, ipTM, …) are
  plausibility filters, not guarantees — say so when reporting, and recommend
  experimental validation for anything consequential.
- For the raw MCP-over-HTTP protocol (curl, or if you can't use the bundled
  client), see [mcp_http.md](./mcp_http.md).
- **Portable:** this whole `proto/` folder is self-contained — copy it into any
  project's `.pantheon/skills/` (or `~/.pantheon`) and set `PROTO_API_KEY`; it
  needs only Python + `httpx`.
