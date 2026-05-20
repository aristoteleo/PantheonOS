---
category: scientific_visualization
description: |
  AI team for autonomous scientific figure production.
  Always produces PNG (required for canvas display). Adds PDF + SVG only when
  the task calls for publication / LaTeX / vector-editable output.
  Covers data-driven plots, BioRender-style conceptual illustrations, and multi-panel
  composite figures. Works with or without a live canvas UI.
icon: 🎨
id: graph_maker_team
name: Graph Maker Team
type: team
version: 1.1.0
agents:
  - graph_maker/leader
  - researcher
  - graph_maker/data_plotter
  - graph_maker/illustrator
---

# Graph Maker Team

A specialized AI team for autonomous scientific figure production. Delivers figures as PNG (always) and optionally PDF + SVG for publication workflows. Covers data-driven plotting, BioRender-style conceptual illustrations, and composite multi-panel figures.

Works in two contexts:
- **With canvas UI** (medrix-scientist): outputs land in `.canvas/assets/`; layout is declared via `agent_output.json` which the frontend merges into the live canvas.
- **Without canvas UI** (API / pipeline): same output format — `agent_output.json` acts as a standalone delivery manifest; PNG files are usable immediately by any downstream tool.

## Team Structure

| Agent | Role | Key Capabilities |
|-------|------|------------------|
| **leader** | Orchestrator | CANVAS_CONTEXT parsing (optional), intent triage, execution depth inference, style card authoring, **layout declaration via agent_output.json**, quality control |
| **researcher** | On-demand specialist | Journal/venue lookup for unknown targets, digestion of user-attached PDFs/datasets/external figures, "in the style of paper X" research. **NOT a default step.** Package installs, routine EDA, and known-journal lookups are NOT routed here. |
| **data_plotter** | Plot producer | Jupyter-based matplotlib/seaborn/plotly figures with internal observe→critic→revise loop, multi-panel composition, format-conditional export (PNG always; PDF+SVG when `style_card.export_formats` includes them). Performs its own EDA inline. |
| **illustrator** | Illustration producer | Methodology / concept / pathway diagrams via a four-phase PaperBanana pipeline (Plan → Style → Render → Critic, T ≤ 3 rounds) using `generate_image` |

## Deliverables

For every finalized figure:
- `{workdir}/.canvas/assets/<name>.png` — always (required for canvas display)
- `{workdir}/.canvas/assets/<name>.pdf` — only when `export_formats` includes "pdf"
- `{workdir}/.canvas/assets/<name>.svg` — only when `export_formats` includes "svg"

Plus:
- `{workdir}/.canvas/agent_output.json` — structured layout manifest (canvas nodes with positions, origins, intents); consumed by frontend if present, otherwise a standalone record
- `{workdir}/.canvas/figure_legends.md` — caption + legend per figure
- `{workdir}/.canvas/figure_manifest.json` — machine-readable index

## Output Format Rule

Leader infers `export_formats` from message intent and writes it into `style_card.json`:

| Signal words in message | export_formats |
|---|---|
| publication / paper / LaTeX / journal / submit / vector / editable | `["png", "pdf", "svg"]` |
| quick / sketch / draft / show me / try / idea | `["png"]` |
| Unclear | `["png"]` (default) — offer publication version at end if task looks heavy |

## Supported Intents

| Intent | Pipeline |
|---|---|
| **data-only** | `data_plotter` only |
| **illustration-only** | `illustrator` (four-phase) → if publication task, `researcher` vectorizes PNG to SVG/PDF |
| **composite-panel** | Both sub-agents in parallel → `data_plotter` composes with svgutils |

## Style Governance

Every task begins with a canonical `{workdir}/inputs/style_card.json` — the single source of truth for DPI, colors, fonts, figure dimensions, and export formats. Sub-agents MUST read and apply the style card; leader enforces consistency across figures.

The `aesthetic_guide` field in `style_card.json` names a style file distributed via the **`figure_styling` skill** (`skills/figure_styling/styles/<aesthetic_guide>.md`). Sub-agents load that file on demand — it is NOT inlined into their system prompts.

**Available style files:**

| aesthetic_guide | Target | Figure class | Key spec |
|---|---|---|---|
| `neurips_diagram` | NeurIPS / ICML / ICLR / CVPR | Methodology / framework diagrams | Soft pastel zones, rounded rects, inward ticks |
| `neurips_plot` | NeurIPS / ICML / ICLR / CVPR | Statistical plots | Open spines, dashed grid, inward ticks, Paul Tol bright |
| `nature_figure` | Nature / Cell / Science | All figure types | 7pt Arial, inward ticks all 4 sides, no grid, Paul Tol bright |
| `ieee_figure` | IEEE journals / conferences | Statistical plots | CM serif, k/r/b/g + linestyle + marker, B&W compatible |
| `custom` / `null` | — | — | Rely only on `style_card.json` + agent defaults |

Conflict priority: **user references > style_card.json > figure_styling/<aesthetic_guide> > agent defaults**.

## Canvas Integration

The team is **canvas-agnostic by default** — it works with or without a live canvas UI.

| Context | canvas.json | CANVAS_CONTEXT | agent_output.json |
|---|---|---|---|
| **Canvas UI active** (medrix) | Present — leader reads for current layout state | Present — carries entry_point, active_frame_id, selection | Frontend reads, merges into canvas, then deletes |
| **No canvas UI** (API/pipeline) | Absent or not provided — leader starts from empty canvas | Absent — leader parses intent from plain text | Stays in workdir as standalone delivery manifest |

### Canvas-mode contract (when UI is active)

- **canvas.json**: leader reads it to understand existing nodes. Never writes to it directly (frontend owns it).
- **agent_output.json**: leader writes this at the end of every turn. Declares every node produced: source path, origin, intent, position, parent frame. Frontend upserts these into the live canvas.
- **Frame snapshots**: frontend renders touched frames to `.canvas/frames/<frame_id>_latest.png` + `_latest.meta.json`. Leader reads these for visual critique when running thorough tasks.
- **CANVAS_CONTEXT block**: when present, carries `entry_point`, `canvas_path`, `active_frame_id`, `selection`. Leader uses these to target the right frame/node without re-reading the entire canvas.
- **Layout responsibility**: node positioning and frame composition are the leader's job — sub-agents are layout-blind and produce assets only.
- **Field discipline**: leader never touches `producer=static` nodes or `locked_by_user=true` nodes.

### Graceful degradation (no canvas UI)

When there is no canvas UI, CANVAS_CONTEXT is absent and canvas.json may not exist. Leader:
1. Treats the message as `entry_point: chat_send`, no active frame, no selection.
2. Skips reading canvas.json (or reads it if it happens to exist).
3. Still writes `agent_output.json` — the same CanvasDocument-schema file serves as both the canvas layout spec and a standalone delivery manifest.
4. Skips frame PNG visual verification (`.canvas/frames/` won't exist).

## Execution Depth

Leader infers execution depth from message language — no explicit mode flag.

| Signal | Behavior |
|---|---|
| quick / sketch / try / draft / show me | Single-shot sub-agent call, skip multi-round critic, return immediately |
| publication / paper / journal / final / polished | Full Plan→Style→Render→Critic loop (2–3 rounds), AskUserQuestion at key decision points, cross-frame visual consistency check |
| Unclear | Lightweight; offer thorough version at end if output looks insufficient |

## Workdir Layout

```
{workdir}/
  environment.md              # plotting dependency audit
  inputs/
    data/                     # user-provided data files
    brief.json                # structured (S, C) brief — MANDATORY
    style_card.json           # canonical style spec (DPI, colors, fonts, export_formats)
    references/               # normalized reference material (if user provided refs)
  drafts/
    notebooks/                # data_plotter intermediates
    illustrations/            # illustrator raw PNGs + plan/style/critic traces
    panels/                   # composite-panel intermediates
  .canvas/
    canvas.json               # existing canvas state (read-only; may be absent)
    agent_output.json         # leader's layout output (write at end of turn)
    assets/                   # final figure deliverables
      Fig1_main.png
      Fig1_main.pdf           # only if export_formats includes "pdf"
      Fig1_main.svg           # only if export_formats includes "svg"
      ...
    frames/                   # frame PNG snapshots (frontend writes; leader reads)
    figure_legends.md
    figure_manifest.json
```

## Core Workflow

1. **Triage**: Classify intent (data / illustration / composite); detect target (journal / slides / web); infer `export_formats`; write `inputs/brief.json`.
2. **Reference detection**: Scan user message for reference figures/URLs/documents; if found, `researcher` normalizes them.
3. **Style card**: Author `inputs/style_card.json` with DPI, fonts, colors, figure sizes, and `export_formats`.
4. **Canvas state** (if canvas.json exists): read relevant frame/node slice based on CANVAS_CONTEXT.
5. **Environment audit** (optional, only if tools missing): `researcher` checks matplotlib/seaborn/plotly/svgutils/Pillow/inkscape.
6. **Figure production** (parallelized across independent figures):
   - Data panels → `data_plotter` with style card injection
   - Conceptual panels → `illustrator` → if publication, `researcher` vectorizes PNG to SVG/PDF
7. **Composition** (composite intent): `data_plotter` composes via svgutils; exports per `export_formats`.
8. **Verification**: leader runs `observe_images` on each PNG; re-delegates on failure.
9. **agent_output.json**: leader writes canvas node declarations for all produced figures with positions, origins, and intents.
10. **Manifest + legends**: write `.canvas/figure_manifest.json` and `.canvas/figure_legends.md`.
11. **Delivery**: concise summary of figure paths returned to user.

## Agent Call Relationships

```
                              [User]
                                │
                                ▼
                          ┌───────────┐
                          │   leader  │
                          └───────────┘
                    ┌──────────┼──────────────────┐
                    │          │                  │
                    ▼          ▼                  ▼
              ┌──────────┐ ┌──────────────┐ ┌──────────────┐
              │researcher│ │ data_plotter │ │ illustrator  │
              └──────────┘ └──────────────┘ └──────────────┘
                   ▲              │                    │
                   └──────────────┴────────────────────┘
                        (sub-agents → researcher for tools/vectorize)
```

| Caller | Can Call | Purpose |
|--------|----------|---------|
| **leader** | `researcher`, `data_plotter`, `illustrator` | Orchestrate end-to-end |
| **data_plotter** | `researcher`, `illustrator` | EDA/tools; request inline illustration sub-panels |
| **illustrator** | `researcher` | Vectorize PNG; install tools |
| **researcher** | _(none)_ | Leaf node — provides services |

---

## End-to-End Interaction Flow (Data Contracts & File Formats)

### Workdir Layout Overview

```
{workdir}/
├── environment.md
├── triage.md                            (optional)
├── inputs/
│   ├── data/
│   ├── brief.json
│   ├── style_card.json
│   └── references/
│       ├── local/
│       └── normalized.json
├── drafts/
│   ├── notebooks/
│   │   ├── <name>.ipynb
│   │   ├── <name>_round<t>.png
│   │   ├── <name>_round<t>.json
│   │   └── <name>_trace.json
│   ├── illustrations/
│   │   ├── <id>_plan.md
│   │   ├── <id>_style.md
│   │   ├── <id>_round<t>.png
│   │   ├── <id>_round<t>.json
│   │   ├── <id>_final.png
│   │   └── <id>_trace.json
│   └── panels/
└── .canvas/
    ├── canvas.json                      (read-only; may be absent)
    ├── agent_output.json                (leader writes at end of turn)
    ├── assets/
    │   ├── Fig1_main.png                (always)
    │   ├── Fig1_main.pdf                (if export_formats includes "pdf")
    │   ├── Fig1_main.svg                (if export_formats includes "svg")
    │   └── ...
    ├── frames/                          (frontend writes; leader reads for vision checks)
    ├── figure_legends.md
    └── figure_manifest.json
```

### Flow Overview (Leader Orchestration)

```
User Message (+ optional CANVAS_CONTEXT)
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1  TRIAGE (leader internal reasoning)               │
│   Classify intent ∈ {data-only, illustration-only,       │
│                      composite-panel}                    │
│   Infer category, aspect_ratio, target, journal,         │
│   export_formats. Do NOT write brief.json yet.           │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2  REFERENCE DETECTION (leader internal scan)       │
│   Scan user message → strong_hits + keyword_hits         │
│                                                          │
│   if has_references:                                     │
│       → Stage A (researcher): normalize material         │
│       → Stage B (researcher, conditional): Top-K pick    │
│       produces: inputs/references/normalized.json        │
│   else: skip retrieval                                   │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3  WRITE brief.json                                 │
│   → inputs/brief.json                                    │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4  WRITE style_card.json                            │
│   Set export_formats based on task intent:               │
│     exploratory → ["png"]                                │
│     publication → ["png","pdf","svg"]                    │
│   → inputs/style_card.json                               │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 5  FIGURE PRODUCTION (parallel per figure)          │
│   data_plotter  (statistical_plot)                       │
│   illustrator   (diagram / illustration)                 │
│   Each sub-agent runs its own internal iteration         │
│   (T ≤ 2–3 rounds). Exports per export_formats.         │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 6  VECTORIZE (illustration, publication only)       │
│   (researcher) inkscape/potrace PNG → SVG/PDF            │
│   Only when export_formats includes "svg" or "pdf"       │
│   → .canvas/assets/<name>.{svg,pdf}                      │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 7  VERIFICATION (leader)                            │
│   observe_images per figure; re-delegate on failure.     │
│   If frame PNG exists: read for cross-frame consistency. │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 8  WRITE agent_output.json                          │
│   Declare all produced nodes with positions, origins,    │
│   intents. Frontend merges if present; file stays as     │
│   standalone manifest otherwise.                         │
│   → .canvas/agent_output.json                            │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 9  MANIFEST & LEGENDS                               │
│   → .canvas/figure_manifest.json                         │
│   → .canvas/figure_legends.md                            │
└──────────────────────────────────────────────────────────┘
     │
     ▼
Step 10  DELIVERY summary → User
```

### Core Data File Schemas

#### `inputs/brief.json`

```json
{
  "intent": "illustration-only",
  "figures": [
    {
      "id": "Fig1",
      "name": "Fig1_framework",
      "category": "agent_reasoning",
      "S_source_context": "...",
      "C_communicative_intent": "...",
      "aspect_ratio": "1.8:1",
      "notes": "Left-to-right narrative flow."
    }
  ],
  "target": "journal",
  "journal": "neurips",
  "audience": "specialist",
  "references": {
    "has_references": true,
    "trigger_reason": "user message contained arxiv URL plus keyword '模仿'",
    "raw_mentions": [
      {"type": "url", "value": "https://arxiv.org/abs/2601.23265", "context": "模仿这篇论文 Fig 2 的风格"}
    ],
    "normalized_path": "{workdir}/inputs/references/normalized.json"
  }
}
```

#### `inputs/style_card.json`

```json
{
  "target": "journal",
  "journal_class": "neurips",
  "aesthetic_guide": "neurips_diagram",
  "dpi_preview": 300,
  "dpi_final": 600,
  "figure_size_inches": {
    "single_column": [3.5, 2.6],
    "double_column": [7.2, 5.0]
  },
  "font_family": "Helvetica",
  "font_size": { "axis_label": 9, "tick": 8, "legend": 8, "title": 10, "panel_letter": 11 },
  "colors": {
    "primary": "#1a365d",
    "secondary": "#2c5282",
    "accent": "#c05621",
    "categorical_palette": ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"],
    "diverging_cmap": "RdBu_r",
    "sequential_cmap": "viridis"
  },
  "line_width": 1.2,
  "export_formats": ["png"],
  "notes": "Exploratory task — PNG only. Switch to [\"png\",\"pdf\",\"svg\"] for submission."
}
```

#### `.canvas/agent_output.json` — canvas layout declaration

Written by leader at the end of every turn. Frontend upserts these nodes if canvas UI is active; otherwise the file remains as a delivery record.

```json
{
  "version": "1.0",
  "nodes": [
    {
      "id": "shape:frame-panel1",
      "type": "frame",
      "x": 100, "y": 100, "width": 900, "height": 650,
      "label": "Figure 1 — Framework Overview",
      "layout": "grid",
      "color": "#7c3aed",
      "children": ["shape:img-fig1"]
    },
    {
      "id": "shape:img-fig1",
      "type": "agent-image",
      "x": 110, "y": 140, "width": 860, "height": 590,
      "source": ".canvas/assets/Fig1_framework.png",
      "producer": "ai",
      "origin": { "kind": "ai", "agent_id": "illustrator", "prompt": "...", "model": "generate_image" },
      "intent": "PaperBanana framework overview with linear planning and iterative refinement loop"
    }
  ],
  "edges": []
}
```

#### `.canvas/figure_manifest.json`

```json
{
  "figures": [
    {
      "id": "Fig1",
      "name": "Fig1_framework",
      "intent": "illustration-only",
      "category": "agent_reasoning",
      "source_agent": "illustrator",
      "formats": {
        "png": "{workdir}/.canvas/assets/Fig1_framework.png"
      },
      "dpi": 300,
      "size_inches": [7.2, 4.0],
      "aspect_ratio": "1.80:1",
      "aesthetic_guide": "neurips_diagram",
      "references_used": [],
      "critic_rounds": 2,
      "caption_file": "{workdir}/.canvas/figure_legends.md#fig1"
    }
  ]
}
```

### Illustrator Internal Data Flow

```
brief.json + style_card.json + normalized.json (optional)
                     │
     ┌───────────────▼─────────────────┐
     │ Phase 0 — References Digest     │  (only when has_references=true)
     │   observe_images(each ref)      │
     │   → <id>_references.md          │
     └─────────────────────────────────┘
                     │
     ┌───────────────▼─────────────────┐
     │ Phase 1 — Plan (semantic)       │
     │   → <id>_plan.md                │
     └─────────────────────────────────┘
                     │
     ┌───────────────▼─────────────────┐
     │ Phase 2 — Style (aesthetic)     │
     │   refs > style_card > guide     │
     │   → <id>_style.md               │
     └─────────────────────────────────┘
                     │
        ┌────────────▼───────────┐
        │ Phase 3 — Render t     │ ←────────┐
        │  generate_image(P*)    │          │
        │  → <id>_round<t>.png  │          │
        └────────────────────────┘          │
                     │                      │
        ┌────────────▼───────────┐          │
        │ Phase 4 — Critic t     │          │
        │  observe_images + JSON │          │
        │  → <id>_round<t>.json │          │
        └────────────────────────┘          │
                     │                      │
          "No changes needed."? ────NO──────┘
                     │YES
                     ▼
            <id>_final.png
                     │
     (if export_formats has pdf/svg)
                     │
     ┌───────────────▼─────────────────┐
     │ researcher vectorizes           │
     │  inkscape → SVG/PDF             │
     └─────────────────────────────────┘
                     │
                     ▼
       .canvas/assets/<name>.{png,[pdf],[svg]}
```

### Data_plotter Internal Data Flow

```
brief.json + style_card.json + normalized.json (optional)
                     │
   ┌─────────────────▼───────────────────┐
   │ Pre-round: Reference Absorption     │  (only when has_references=true)
   │   observe_images → rcParams overrides│
   └─────────────────────────────────────┘
                     │
   ┌─────────────────▼───────────────────┐
   │ Notebook Setup                      │
   │   Cell 1: imports                   │
   │   Cell 2: rcParams (+ ref overrides)│
   │   Cell 3+: load data + plot         │
   └─────────────────────────────────────┘
                     │
        ┌────────────▼───────────┐
        │ Round 0 Render         │ ←────────┐
        │  savefig PNG preview   │          │
        └────────────────────────┘          │
                     │                      │
        ┌────────────▼───────────┐          │
        │ Round 0 Critic         │          │
        │  observe_images + JSON │          │
        └────────────────────────┘          │
                     │                      │
          "No changes needed."? ────NO──────┘
                     │YES
                     ▼
   ┌─────────────────────────────────────┐
   │ Final Export                        │
   │  PNG always                         │
   │  PDF, SVG only if in export_formats │
   └─────────────────────────────────────┘
                     │
                     ▼
       .canvas/assets/<name>.png [+ .pdf] [+ .svg]
```

### Priority Chain

```
User message intent
  > user references (raw_mentions)
    > normalized.json visual summaries
      > style_card.json
        > aesthetic_guide file (figure_styling skill)
          > agent built-in defaults
```

### Three Typical Scenarios

#### Scenario A — Exploratory, no canvas UI

```
User (API call): "Draw a quick Transformer architecture diagram"
  │
Step 1: intent=illustration-only, export_formats=["png"]
Step 2: has_references=false
Step 4: style_card.export_formats=["png"]
Step 5: illustrator → Phase 1-4, T=2 rounds → _final.png
Step 6: SKIP (png only)
Step 8: agent_output.json written (standalone manifest)
→ .canvas/assets/Fig1_transformer.png
```

#### Scenario B — Publication, with canvas UI

```
User (canvas): "Publication-ready volcano plot for Nature submission"
  │
Step 1: intent=data-only, export_formats=["png","pdf","svg"]
Step 4: style_card.export_formats=["png","pdf","svg"]
Step 5: data_plotter → T=3 critic rounds → savefig all three formats
Step 7: leader reads frame PNG snapshot for visual consistency
Step 8: agent_output.json → frontend merges node at correct position
→ .canvas/assets/Fig1_volcano.{png,pdf,svg}
```

#### Scenario C — With reference + data

```
User: "Mimic https://arxiv.org/abs/xxx's style; plot acc vs params"
  + /data/results.csv
  │
Step 2: URL + keyword → researcher normalizes → normalized.json
Step 4: export_formats=["png"] (quick by default)
Step 5: data_plotter
         Pre-round: observe refs → rcParams overrides
         Round 0–2: critic loop
         Final: savefig PNG only
Step 8: agent_output.json written
```
