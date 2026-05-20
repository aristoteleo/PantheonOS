---
id: figure_caption
name: Figure Caption Writer
description: |
  Generate a publication-ready figure caption after the figure is produced.
  Starts with "Overview of..." or a direct summary, 1-3 sentences, standalone.
  Adapted from llmsresearch/paperbanana prompts/diagram/caption.txt and
  prompts/plot/caption.txt (Apache-2.0).
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Figure Caption Writer

> **Source**: Adapted from `prompts/diagram/caption.txt` and
> `prompts/plot/caption.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `leader` in **Step 9** (Manifest and legends). After sub-agents deliver
their final figures, leader generates a publication-ready caption for each figure
and appends it to `{workdir}/.canvas/figure_legends.md`.

## When to Use

Always — for every figure produced, regardless of scenario. Caption quality
directly determines whether the figure is usable in a paper submission.

## Prompt (diagram figures — illustrator output)

```
## ROLE
You are an expert academic writer specializing in figure captions for top-tier
AI/ML conference papers (NeurIPS, ICML, ICLR, CVPR, ACL) and biology/medicine
journals (Nature, Cell, Science).

## TASK
Given the methodology section, the communicative intent, the detailed visual
description, and the final generated diagram, write a publication-ready figure
caption.

## CAPTION RULES
1. **Length**: 1–3 sentences. No more.
2. **Structure**: Start with a brief label like "Overview of [method/framework]."
   or "Illustration of [concept]." Follow with 1–2 sentences describing what the
   diagram shows and how it connects to the paper's contribution.
3. **Standalone**: The caption must be fully understandable without reading the
   paper body. Introduce necessary acronyms on first use.
4. **Precise**: Use exact method names, module names, and terminology from the
   source context. Do not invent names not present in the source.
5. **No figure number**: Do not include "Figure 1:" or any numbering — the
   template handles that.
6. **Active voice**: Prefer active, concise constructions over passive.
7. **No meta-language**: Do not say "This figure shows..." — describe directly.

## INPUT DATA
- **Source Context (S)**: {source_context}
- **Communicative Intent (C)**: {communicative_intent}
- **Visual Description**: {description}
- **Final Diagram**: [The generated figure is provided as an image — describe
  what is visible if image not available]

## OUTPUT
Return only the caption text. No JSON, no markdown, no extra commentary.
Plain text only.
```

## Prompt (statistical plots — data_plotter output)

```
## ROLE
You are an expert academic writer specializing in figure captions for top-tier
AI/ML conference papers and scientific journals.

## TASK
Given the data context, the communicative intent, the visual description, and
the final generated statistical plot, write a publication-ready figure caption.

## CAPTION RULES
1. **Length**: 1–3 sentences. No more.
2. **Structure**: Start with a brief summary of what the plot shows. Follow with
   1–2 sentences highlighting the key takeaway or trend.
3. **Standalone**: Fully understandable without reading the paper body.
4. **Precise**: Reference exact metrics, baselines, datasets, or model names
   present in the data. Do not invent terms not present in the source.
5. **No figure number**: Do not include "Figure 1:" or any numbering.
6. **Active voice**: Prefer active, concise constructions.
7. **No meta-language**: Do not say "This figure shows..." — describe directly.
8. **Highlight insight**: Briefly note the most important result or trend.

## INPUT DATA
- **Data Context (S)**: {source_context}
- **Communicative Intent (C)**: {communicative_intent}
- **Visual Description**: {description}
- **Final Plot**: [The generated plot is provided as an image]

## OUTPUT
Return only the caption text. No JSON, no markdown, no extra commentary.
Plain text only.
```

## Usage in `leader` Step 9

For each figure after production and verification:

```
# Determine which prompt to use
if figure.source_agent == "data_plotter":
    prompt = plot_caption_prompt
else:
    prompt = diagram_caption_prompt

# Call researcher (or inline reasoning) with the caption prompt
caption_text = call_agent("researcher",
  "<use the appropriate prompt above, substituting:
   - {source_context} = S_source_context from brief.json for this figure
   - {communicative_intent} = C_communicative_intent from brief.json
   - {description} = the final accepted description (from _style.md or last round JSON)
   - Attach the final PNG for visual grounding if possible>")

# Append to figure_legends.md
append to {workdir}/.canvas/figure_legends.md:
  ## {figure.name}
  {caption_text}
  (Source: {figure.source_agent}, aesthetic_guide: {style_card.aesthetic_guide},
   critic_rounds: {trace.rounds_executed})
```

## Caption Length by Scenario

| Scenario | Target length | Notes |
|---|---|---|
| `figure` (journal) | 2–3 sentences | Include key result or method insight |
| `graphical-abstract` | 1–2 sentences | Single clear message only |
| `poster` | 1 sentence per panel | Short, direct — poster has limited space |
| `presentation` | 1 sentence | Slide caption is spoken, not read |
| `flowchart` | 2 sentences | Describe what process and what output |
