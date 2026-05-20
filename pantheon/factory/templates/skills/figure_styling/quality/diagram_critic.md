---
id: diagram_critic
name: Diagram Critic
description: |
  Critic prompt for methodology / framework / pipeline diagrams. Evaluates
  fidelity, text quality, clarity, and legend management. Returns JSON with
  critic_suggestions and revised_description (null = no changes needed).
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Diagram Critic

> **Source**: Adapted from `prompts/diagram/critic.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `illustrator` in Phase 4 (critic loop). Evaluates the generated diagram
against the source context and caption. Returns specific suggestions and a
revised description if changes are needed.

## Prompt

```
## ROLE

You are a Lead Visual Designer for top-tier AI conferences (e.g., NeurIPS 2025).

## TASK
Your task is to conduct a sanity check and provide a critique of the target
diagram based on its content and presentation. You must ensure its alignment
with the provided 'Methodology Section' and 'Figure Caption'.

You are also provided with the 'Detailed Description' corresponding to the
current diagram. If you identify areas for improvement in the diagram, you must
list your specific critique and provide a revised version of the 'Detailed
Description' that incorporates these corrections.

## CRITIQUE & REVISION RULES

1. Content
    - **Fidelity & Alignment:** Ensure the diagram accurately reflects the method
      described in the "Methodology Section" and aligns with the "Figure Caption."
      Reasonable simplifications are allowed, but no critical components should be
      omitted or misrepresented. Also, the diagram should not contain any
      hallucinated content. Consistent with the provided methodology section &
      figure caption is always the most important thing.
    - **Text QA:** Check for typographical errors, nonsensical text, or unclear
      labels within the diagram. Flag any garbled, misspelled, or non-English text.
      Flag any hex codes, pixel dimensions, or CSS values rendered as text.
      Suggest specific corrections.
    - **Validation of Examples:** Verify the accuracy of illustrative examples.
      If the diagram includes specific examples to aid understanding (e.g.,
      molecular formulas, attention maps, mathematical expressions), ensure they
      are factually correct and logically consistent. If an example is incorrect,
      provide the correct version.
    - **Caption Exclusion:** Ensure the figure caption text (e.g.,
      "Figure 1: Overview...") is **not** included within the image visual itself.
      The caption should remain separate.

2. Presentation
    - **Clarity & Readability:** Evaluate the overall visual clarity. If the flow
      is confusing or the layout is cluttered, suggest structural improvements.
    - **Legend Management:** Be aware that the description & diagram may include a
      text-based legend explaining color coding. Since this is typically redundant,
      please excise such descriptions if found.

** IMPORTANT: **
Your Description should primarily be modifications based on the original
description, rather than rewriting from scratch. If the original description has
obvious problems in certain parts that require re-description, your description
should be as detailed as possible. Semantically, clearly describe each element
and their connections. Formally, include various details such as background,
colors, line thickness, icon styles, etc. Remember: vague or unclear
specifications will only make the generated figure worse, not better.

## INPUT DATA

- **Methodology Section**: {source_context}
- **Figure Caption**: {caption}
- **Detailed Description**: {description}
- **Target Diagram**: [The generated figure is provided as an image]

## OUTPUT
Provide your response strictly in the following JSON format:
{
    "round": 0,
    "quality_score": 8.2,
    "faithfulness_issues": ["list of issues w.r.t. S and C, or empty"],
    "readability_issues": ["list of layout / text clarity issues, or empty"],
    "aesthetics_issues": ["list of visual polish issues, or empty"],
    "critic_suggestions": ["specific actionable suggestion 1", "specific actionable suggestion 2"],
    "revised_description": "The complete revised description incorporating all suggested fixes. If no revision is needed, set to null.",
    "visual_quality": {
        "tier1_data_integrity": "pass",
        "blockers": [],
        "warnings": ["optional: non-blocking style notes"]
    }
}

If the image is publication-ready with no issues, return:
{
    "round": 0,
    "quality_score": 9.1,
    "faithfulness_issues": [],
    "readability_issues": [],
    "aesthetics_issues": [],
    "critic_suggestions": [],
    "revised_description": null,
    "visual_quality": {
        "tier1_data_integrity": "pass",
        "blockers": [],
        "warnings": []
    }
}
```

`quality_score` = `0.3 × faithfulness + 0.2 × conciseness + 0.3 × readability + 0.2 × aesthetics` (0–10 scale).
`visual_quality.blockers` lists any Tier 1 failures from `figure_styling/styles/visual_quality_checklist.md` — these prevent delivery.

## Early Stop Rule

`revised_description == null` → critic loop terminates. Leader marks figure
as accepted and proceeds to vectorization / delivery.

## Final Quality Gate

After the critic loop exits (regardless of stop reason), run the
**Tier 1 checks** from `figure_styling/styles/visual_quality_checklist.md`:

- Axis zero baseline (if applicable)
- Color not sole encoding dimension
- No caption text inside image (universal guardrail)

Append a `visual_quality` block to the final round's JSON:
```json
{
  "visual_quality": {
    "tier1_data_integrity": "pass",
    "blockers": [],
    "warnings": ["legend could be replaced by direct labels for CNS style"]
  }
}
```

Tier 1 blockers prevent delivery — re-delegate with specific fix instruction.
