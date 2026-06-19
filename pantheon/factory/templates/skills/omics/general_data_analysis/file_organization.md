---
id: file_organization
name: Workspace File Organization
description: |
  Keep the workspace tidy: give every analysis task its own descriptively-named
  folder, with figures / data / reports organized inside it — never scatter
  files at the workspace root or dump figures into internal dirs.
tags: [organization, workspace, folders, figures, outputs, hygiene]
---

# Workspace File Organization

A clean workspace makes results reproducible and browsable. The workspace root
is shared across many analyses — treat it like a projects directory, not a
dumping ground. **One analysis task → one self-contained folder.**

> The hard rule is always in your prompt (`<task_brain_dir>`). This skill is the
> detailed reference: naming, layout, figures, and reuse.

---

## The rule

For each distinct analysis, **first create a dedicated, descriptively-named
folder** directly under the workspace root, then keep everything that task
produces inside it.

- ✅ `scatac_pbmc5k/`, `visium_brain_qc/`, `xenium_breast_dea/`
- ❌ loose files at the root: `analysis.ipynb`, `01_qc.png`, `result.h5ad` …
- ❌ figures in `.pantheon/images/` (that's an internal preview dir, not a deliverable home)
- ❌ a generic `outputs/` shared across unrelated analyses

Name the folder for the **analysis + dataset**, lowercase, `snake_case`, no
spaces. Add a short suffix if you run variants (`..._v2`, `..._macs3`).

**Declare this folder at PLANNING time** via `task_boundary`'s `output_dir`
(e.g. `output_dir="scatac_pbmc5k"`) — even before it exists. The Output panel
live-previews it, so the user watches results land as you produce them, before
you register anything.

---

## Standard layout

```
<workspace_root>/
  scatac_pbmc5k/                  ← one folder per analysis task
    scatac_pbmc5k.ipynb           ← the analysis notebook
    figures/                      ← all plots/figures for THIS task
      01_qc_metrics.png
      02_umap_celltypes.png
      06_summary_figure.png
    data/                         ← intermediate + result data
      pbmc5k_processed.h5ad
    report.md                     ← optional written summary
```

Set this up at the start, e.g.:

```python
import os
TASK_DIR = "scatac_pbmc5k"
FIG_DIR  = os.path.join(TASK_DIR, "figures")
DATA_DIR = os.path.join(TASK_DIR, "data")
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

fig.savefig(os.path.join(FIG_DIR, "01_qc_metrics.png"), dpi=150, bbox_inches="tight")
adata.write(os.path.join(DATA_DIR, "pbmc5k_processed.h5ad"))
```

Use **relative paths** rooted at the task folder — not absolute
`/Users/.../.pantheon/images` paths.

---

## Figures

Save deliverable figures into `<task_folder>/figures/`. The Output panel
live-previews your declared task folder, so figures appear to the user as you
create them; then register the real ones with `register_output`.

- Do NOT dump figures into `.pantheon/images/` or the workspace root.
- `.pantheon/images/` is only for a quick throwaway plot you want pushed inline
  into the chat right now (e.g. on a messaging channel) — never for deliverables.

---

## Registering outputs

When the task is done, register the **task folder** (or its `figures/`
subfolder) with `register_output` so the user can browse it in the Output panel.
Prefer registering the folder over each individual file.

---

## Reuse vs. new

Before creating a folder, glance at the workspace root:

- **Continuing** earlier work on the same analysis? Reuse that task folder.
- **New / unrelated** analysis? Make a new folder — don't graft it onto an
  existing one.
