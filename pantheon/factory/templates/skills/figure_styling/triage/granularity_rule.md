---
id: granularity_rule
name: Granularity Rule
description: |
  Determine if a user request should be split into multiple figures. Prevents
  cramming structure + progression + execution into one unusable figure.
source: https://github.com/SHALINS428/Codex-drawio-skill
license: MIT
---

# Granularity Rule

> **Source**: Adapted from `skill/drawio/references/figure-types.md` (Granularity Rule
> section) in [SHALINS428/Codex-drawio-skill](https://github.com/SHALINS428/Codex-drawio-skill)
> (MIT), and `references/docs/academic-figure-playbook.md` in
> [bahayonghang/drawio-skills](https://github.com/bahayonghang/drawio-skills) (MIT).

## The Rule

Before drawing, ask: **Is this figure explaining structure, progression, or execution?**

| Question | Figure type |
|---|---|
| What modules exist and how do they relate? | → System Architecture |
| How does the study/method progress over time? | → Technical Roadmap |
| What are the ordered steps and decision branches? | → Workflow / Process |

**Do not collapse all three into one overloaded figure.**

If a figure tries to explain structure, chronology, and detailed control flow
simultaneously — split it into two or three figures.

## Mixing Signals (split triggers)

| Combination | Decision |
|---|---|
| "架构" + "流程" (structure + process) | **Split** |
| "模块" + "步骤" (modules + steps) | **Split** |
| "系统图" + "统计数据" (diagram + data plot) | **Split** |
| "阶段" + "分支逻辑" (stages + branching) | **Split** |
| "方法概述" + "实验结果" (method + results) | **Split** |
| All elements belong to one concern | **Single figure** |

## Detection Logic

```
1. Extract all figure-type keywords from user message + S_source_context
2. Count how many distinct figure types are implied
3. If count == 1 → single figure, proceed
4. If count >= 2 → should_split = true, suggest decomposition
```

## Output (add to brief.json)

**Should not split:**
```json
{
  "should_split": false,
  "reason": "All elements are structural — modules, boundaries, data flows. No temporal progression or execution branches."
}
```

**Should split:**
```json
{
  "should_split": true,
  "reason": "Request mixes System Architecture (系统架构) + Technical Roadmap (四个阶段) + Statistical Plot (损失曲线).",
  "suggested_split": [
    {
      "figure_id": "Fig1",
      "figure_type": "System Architecture",
      "scope": "Overall system modules and data boundaries",
      "routing": "illustrator"
    },
    {
      "figure_id": "Fig2",
      "figure_type": "Technical Roadmap",
      "scope": "Four-stage training pipeline progression",
      "routing": "illustrator"
    },
    {
      "figure_id": "Fig3",
      "figure_type": "Statistical Plot",
      "scope": "Training and validation loss curves",
      "routing": "data_plotter"
    }
  ]
}
```

## Leader Action When `should_split == true`

If the split is obvious (≥2 clearly distinct concerns) → decompose into
multiple figure records in `brief.json` and proceed without asking the user.

If the split is ambiguous → surface one-liner to user:
> "您的需求包含多个图型（架构图 + 流程图），建议生成 2 张图。是否继续？"

If user declines → generate as single composite-panel figure and note in
`figure_legends.md` that the figure combines multiple concerns.
