---
id: diagram_planner
name: Diagram Planner
description: |
  Phase 1 planning prompt for methodology/framework/pipeline diagrams.
  Takes S_source_context + C_communicative_intent + reference examples,
  outputs a detailed semantic description with aspect ratio recommendation.
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Diagram Planner

> **Source**: `prompts/diagram/planner.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `illustrator` in **Phase 1 (Plan)**. Takes raw methodology text and
a figure caption, outputs the most detailed possible semantic description of
the diagram — without any aesthetic specification.

## When to Use

- Phase 1 of every illustrator task
- Especially valuable when references are provided (they are passed as `{examples}`)

## Prompt

```
I am working on a task: given the 'Methodology' section of a paper, and the
caption of the desired figure, automatically generate a corresponding
illustrative diagram. I will input the text of the 'Methodology' section,
the figure caption, and your output should be a detailed description of an
illustrative figure that effectively represents the methods described in the
text.

To help you understand the task better, and grasp the principles for
generating such figures, I will also provide you with several examples.
You should learn from these examples to provide your figure description.

** IMPORTANT: **
Your description should be as detailed as possible. Semantically, clearly
describe each element and their connections. Formally, include various details
such as background style (typically pure white or very light pastel), colors,
line thickness, icon styles, etc. Remember: vague or unclear specifications
will only make the generated figure worse, not better.

Your description should cover:
1. **Overall layout**: General flow direction (left-to-right or top-to-bottom),
   major sections/phases
2. **Components**: Each box, module, or element with its exact label
3. **Connections**: Arrows, data flows, and their directions
4. **Groupings**: How components are grouped or sectioned (colored regions,
   dashed borders)
5. **Labels and annotations**: Text labels, mathematical notations
6. **Input/Output**: What enters and exits the system
7. **Styling**: Background fills, color palettes (in natural language, e.g.,
   "soft sky blue", "warm peach" — never hex codes), line weights, icon styles

## Methodology Section
{source_context}

## Figure Caption
{caption}

## Reference Examples
{examples}

Based on the methodology section, figure caption, and learning from the style
and structure of the reference examples above, generate a comprehensive and
detailed textual description of the methodology diagram.

Note: Do not include figure titles (e.g., "Figure 1: ...") in the diagram
description. The caption should remain separate from the diagram content.

## Aspect Ratio Recommendation

After your detailed description, on a **new line**, output exactly one line
in this format:
RECOMMENDED_RATIO: <ratio>
where <ratio> is one of: {supported_ratios}.

Choose the best aspect ratio based on:
- The **content structure**: pipelines and sequential flows → wide (16:9,
  21:9); deep hierarchies or vertical stacks → tall (2:3, 9:16); balanced
  architectures → square-ish (1:1, 4:3, 3:4)
- The **reference examples' aspect ratios** listed above (if available)
- The **number of components** and their spatial arrangement

For example, a left-to-right encoder-decoder pipeline would be 16:9, while
a top-to-bottom tree structure would be 2:3.
```

## Notes for `illustrator`

- `{source_context}` = `S_source_context` from brief.json (enriched by context_enricher if run)
- `{caption}` = `C_communicative_intent` from brief.json (sharpened by caption_sharpener if run)
- `{examples}` = reference figure observations from `<id>_references.md` (Phase 0), or empty if no references
- `{supported_ratios}` = the set of aspect ratios your image-gen model supports
- Save output to `{workdir}/drafts/illustrations/<id>_plan.md`
