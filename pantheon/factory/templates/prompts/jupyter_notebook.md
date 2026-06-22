---
id: jupyter_notebook
name: Jupyter Notebook Usage
description: |
  When to reach for a notebook vs. scripts, and how file paths resolve inside a
  notebook vs. your other tools. Included by agents that carry the
  integrated_notebook toolset.
---

### Notebooks vs. Scripts — pick the right medium

When the work is **data analysis, exploration, or scientific computation**, do
it in an **integrated notebook** (`integrated_notebook`), not ad-hoc scripts. A
notebook keeps narrative, code, and outputs (tables, figures) together in one
re-runnable document — which is exactly what makes analysis **reproducible and
readable**. Default to a notebook for: EDA, statistical analysis,
plotting/visualization, iterative data exploration, and any "analyze / explore /
investigate this data" request. Register the notebook as a deliverable with
`register_output`.

Use **scripts, direct file edits, or the shell** for everything else: quick
one-off operations, data/file wrangling utilities, **software development**
(building tools, libraries, apps), and writing or running **tests**. These don't
benefit from a notebook's narrative format and belong in version-controlled
source files.

When a task mixes both (e.g., build a small tool, then analyze its output), use
a script for the tooling and a notebook for the analysis.

### Notebook file paths

A notebook's kernel runs with its working directory set to the **notebook's own
folder** (standard Jupyter), so relative paths in a cell resolve **next to the
`.ipynb`**, not at the workspace root:

```python
# cell in scatac_pbmc500/analysis.ipynb
plt.savefig("figures/umap.png")        # -> scatac_pbmc500/figures/umap.png
```

Your non-notebook tools — file manager, shell, `register_output`, `observe_images`
— operate from the **workspace root**. So to read, verify, or register a file a
notebook wrote, give its path from the workspace root (include the notebook's
folder):

```python
register_output("scatac_pbmc500/figures/umap.png")   # not "figures/umap.png"
```

Absolute paths resolve identically from any tool — use them when you want to avoid
the relative-vs-root distinction entirely.
