---
id: figure_triage_index
name: Figure Triage Rules Index
description: |
  Triage rules for figure type classification and routing decisions.
  Run in Phase 1 (after input optimization, before production).
  Adapted from SHALINS428/Codex-drawio-skill (MIT) and
  bahayonghang/drawio-skills (MIT).
---

# Figure Triage Rules

These rules help leader classify the user's request and make routing decisions
before committing to sub-agent production.

## Rules

| Rule | File | Purpose | When to use |
|---|---|---|---|
| `figure_type_classifier` | [figure_type_classifier.md](./figure_type_classifier.md) | Classify into Architecture / Roadmap / Workflow / Plot | Every request before routing |
| `granularity_rule` | [granularity_rule.md](./granularity_rule.md) | Decide if request should be split into multiple figures | When request mentions multiple concerns |

## Usage (leader)

```
Phase 1: Intent Triage (after brief.json is initialized)

1. Run figure_type_classifier on S_source_context + C_communicative_intent
   → get figure_type (Architecture / Roadmap / Workflow / Statistical Plot /
     Conceptual Framework / Schematic)
   → write to brief.json as category

2. If figure_type is ambiguous OR user mentions multiple concerns:
   → run granularity_rule
   → if should_split == true: decompose into multiple figure records in brief.json

3. Route based on figure_type:
   - Statistical Plot → data_plotter
   - all others → illustrator (or composite if mixed)
```
