---
category: bioinformatics
description: |
  Specialized team for OmicVerse-driven single-cell and spatial omics analysis.
  Uses OmicVerse-native lookup APIs from the same Python runtime that executes
  notebooks, instead of relying on a separate Pantheon omicverse toolset.
icon: 🧬
id: omicverse_team
name: OmicVerse Team
type: team
version: 2.0.0
agents:
  - omicverse_leader
  - omicverse_expert
  - researcher
  - scientific_illustrator
---

# OmicVerse Team

A built-in team template for OmicVerse analysis projects where API lookup and
execution happen in the same Python runtime.

## Team Structure

| Agent | Role | Responsibility |
|-------|------|----------------|
| **omicverse_leader** | Coordinator | Plans work, organizes the workdir, delegates execution and support tasks |
| **omicverse_expert** | Execution specialist | Runs OmicVerse/scverse workflows with notebook execution and OmicVerse-native lookup APIs |
| **researcher** | Research support | Literature search, docs lookup, environment/context investigation |
| **scientific_illustrator** | Figure specialist | Produces polished scientific figures and visual summaries |

## Core Rule

The OmicVerse execution agent must not rely on a Pantheon `omicverse` toolset.
Instead, it must first use `python_interpreter` to call OmicVerse lookup
helpers for planning, and only then use `integrated_notebook` to generate and
execute the final notebook. These lookup calls are planning-time steps, not
notebook cells to keep in the final deliverable.

Low-level fallback inside the same runtime:

```python
scanner = ov.utils.RegistryScanner()
scanner.ensure_runtime_registry()
scanner.load_static_entries()

skill_registry, overview = ov.utils.initialize_skill_registry()
```

## Recommended Workflow

1. Clarify the biological question and identify available datasets.
2. Establish one absolute `workdir` and inspect existing outputs.
3. Delegate core analysis to `omicverse_expert`.
4. Use `researcher` for literature, environment checks, or external documentation.
5. Use `scientific_illustrator` for publication-ready figure refinement.
6. Deliver a concise synthesis with output paths and follow-up recommendations.

## Team Guidance

- Prefer OmicVerse-native APIs when they exist.
- Require `python_interpreter`-based `registry_summary()` and concrete API lookup before any `integrated_notebook` work begins.
- Use lookup per analysis stage, not once per API call. Start broad only when exploring, then narrow to exact APIs before coding a stage.
- Treat lookup as an internal planning step. The final notebook should contain the selected analysis workflow, not the lookup commands themselves, unless the user explicitly asks for an introspection notebook.
- Keep notebook execution and OmicVerse imports in the same runtime.
- Save datasets, notebooks, figures, and reports in stable subdirectories under the workdir.
