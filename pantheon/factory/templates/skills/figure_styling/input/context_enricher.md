---
id: context_enricher
name: Context Enricher
description: |
  Structure raw methodology text into diagram-ready content: components,
  data flows, groupings, I/O, key relationships, and operations.
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Context Enricher

> **Source**: Adapted from `prompts/diagram/context_enricher.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Transform vague user input (e.g. "帮我画个 Transformer 架构图") into structured,
diagram-ready content before passing to `illustrator` or `data_plotter`.

## When to Use

- User provides only a topic or high-level description without explicit components or flow
- User input is prose without named modules, arrows, or groupings
- Source context in `brief.json` would be too vague for the illustrator to plan from

## Prompt (leader inlines this when calling `researcher` or `illustrator`)

```
You are an expert at analyzing academic methodology sections and restructuring
them for diagram generation. Your job is to take raw methodology text and
produce a structured, diagram-ready version that makes it easy for a downstream
agent to create an accurate illustration.

## Your Task

Analyze the methodology text below and produce a restructured version that
explicitly identifies:

1. **Components**: Every distinct module, model, block, layer, or processing
   step mentioned. Give each a clear, concise label.
2. **Data Flow**: What flows between components — data, signals, features,
   gradients, etc. Identify the direction (input → output).
3. **Groupings**: Which components belong together (e.g., "Encoder block",
   "Training pipeline", "Preprocessing stage"). Name each group.
4. **Input/Output**: What enters the system and what comes out. Be explicit
   about data types and shapes if mentioned.
5. **Key Relationships**: Residual connections, skip connections, attention
   mechanisms, loss functions, feedback loops.
6. **Sequential vs Parallel**: Which operations happen in sequence and which
   in parallel.
7. **Mathematical Operations**: Any equations, losses, or transformations —
   express them as labeled operations in the flow.

## Rules
- Preserve ALL technical details from the original — do not summarize or lose information
- Add structure and clarity, but never invent components not in the source
- Use the caption to understand what aspects of the methodology to emphasize
- Output as structured text (not JSON), using clear headers and bullet points
- If the text describes multiple variants or ablations, focus on the main architecture

## Methodology Text
{source_context}

## Figure Caption (for context on what to emphasize)
{caption}

## Structured Output
Produce the restructured methodology below:
```

## Output

Structured text (headers + bullet points) covering components, data flow,
groupings, I/O, key relationships, sequential/parallel, math operations.

Leader writes this to `{workdir}/inputs/brief.json` as `S_source_context`
for the relevant figure.
