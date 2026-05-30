---
id: figure_input_index
name: Figure Input Optimization Index
description: |
  Input optimization prompts for the Graph Maker Team. Run before figure
  generation to structure vague user input into diagram-ready content,
  and to select Top-K reference figures for few-shot visual learning.
  Adapted from llmsresearch/paperbanana (Apache-2.0).
---

# Figure Input Optimization

These prompts help leader optimize raw user input before passing to sub-agents.

## Modules

| Module | File | Purpose | When to use |
|---|---|---|---|
| `context_enricher` | [context_enricher.md](./context_enricher.md) | Structure methodology text into components / flows / groupings | User input is vague prose, no explicit structure |
| `caption_sharpener` | [caption_sharpener.md](./caption_sharpener.md) | Sharpen vague caption into precise visual specification | Caption is generic ("流程图", "架构图", "Figure 1") |
| `reference_retriever` | [reference_retriever.md](./reference_retriever.md) | Select Top-K reference figures from normalized pool (Stage B) | `has_references == true` AND pool > K=5 entries |

## Usage (leader)

```
Phase 0: Input Optimization (before intent triage)

1. If user input is vague methodology text → run context_enricher
   → writes structured content to {workdir}/inputs/brief.json S_source_context

2. If caption is missing or generic → run caption_sharpener
   → writes sharpened caption to {workdir}/inputs/brief.json C_communicative_intent

3. Proceed to intent triage with enriched brief.json

Reference detection (Phase 0b — parallel with 1 and 2):

4. If has_references == true:
   → Stage A: call researcher to normalize all reference materials
     → {workdir}/inputs/references/normalized.json
   → Stage B: if pool > 5 entries, use reference_retriever.md prompt
     → appends "selected" key to normalized.json
   Sub-agents receive normalized.json path and observe selected references.
```

