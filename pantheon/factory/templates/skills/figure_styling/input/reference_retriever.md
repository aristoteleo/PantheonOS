---
id: reference_retriever
name: Reference Retriever
description: |
  Select Top-K reference figures from a normalized candidate pool for
  few-shot visual learning. Ranks candidates by research domain match
  and visual intent match. Used in leader Stage B.
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Reference Retriever

> **Source**: Adapted from `prompts/diagram/retriever.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `leader` in **Stage B** of reference detection. Selects the most
relevant Top-K entries from `{workdir}/inputs/references/normalized.json`
to serve as few-shot visual examples for `illustrator` or `data_plotter`.

**Stage A** (material normalization via `researcher`) runs first and produces
`normalized.json`. Stage B runs only when the pool has > K=5 OK entries.

## Prompt (leader passes this to `researcher` in Stage B call)

```
You are acting as a Reference Retriever for the Graph Maker Team.
Workdir: {workdir}.

TARGET:
- S_source_context: <verbatim from the user's request>
- C_communicative_intent: <one-line figure intent>
- category: <from triage — one of: agent_reasoning | vision_perception |
  generative_learning | science_applications | statistical_plot | mixed>

CANDIDATE POOL: {workdir}/inputs/references/normalized.json
(Read entries where status == "ok". Ignore entries with status == "failed".)

SELECTION RULES (priority order, adapted from PaperBanana):

1. BEST:  same category AND same visual intent
   Example: target is "Agent Framework" diagram → pick other "Agent Framework" diagrams
2. OK:    same visual intent, different category
   (Visual structure matters more than research topic for drawing quality)
3. AVOID: different visual intent
   Example: target is a Pipeline diagram → avoid Bar-chart candidates

**Visual intent types** (infer from visual_summary and category_guess):
- Framework / Pipeline (sequential flow, left-to-right)
- Architecture / Module (spatial relationships, boundaries)
- Roadmap / Timeline (progression, stages)
- Schematic / Pathway (biological or chemical flow)
- Statistical Plot (bar, line, scatter, heatmap)
- Conceptual / Abstract (theory, relationships)

K = 5.

OUTPUT (strict JSON) → append to {workdir}/inputs/references/normalized.json
under the key "selected":
{
  "selected_ids": ["ref_1", "ref_42", ...],
  "rationale_per_pick": {
    "ref_1": "Same category (agent_reasoning) + same visual intent (Framework pipeline)",
    "ref_42": "Different category but same left-to-right pipeline structure"
  }
}

Do NOT modify existing entries. Only append the "selected" key.
```

## Integration with leader Stage B

Leader's `call_agent("researcher", ...)` for Stage B uses this prompt verbatim,
substituting `{workdir}`, `S_source_context`, `C_communicative_intent`, and
`category` from the current task context.

If Stage B is skipped (pool ≤ K entries), set `selected_ids` to all `ok` entry IDs.
