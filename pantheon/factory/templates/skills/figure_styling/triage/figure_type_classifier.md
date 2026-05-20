---
id: figure_type_classifier
name: Figure Type Classifier
description: |
  Classify user request into one of six figure types, each with distinct
  routing, quality focus, and generation strategy.
source: https://github.com/SHALINS428/Codex-drawio-skill
license: MIT
---

# Figure Type Classifier

> **Source**: Adapted from `skill/drawio/references/figure-types.md` in
> [SHALINS428/Codex-drawio-skill](https://github.com/SHALINS428/Codex-drawio-skill)
> (MIT), and `references/docs/academic-figure-playbook.md` in
> [bahayonghang/drawio-skills](https://github.com/bahayonghang/drawio-skills) (MIT).

## Purpose

Classify the user's request into one of six figure types before routing to
sub-agents. Each type has different routing, quality focus, and generation
strategy.

## Figure Types

### System Architecture

**Use when reader needs to understand**: what modules exist, how modules relate,
where data enters/leaves, how runtime responsibilities are divided.

**Typical nodes**: data source, preprocessing layer, model layer, retrieval
layer, output/application layer.

**Quality focus**: group major subsystems clearly; show boundaries and
interaction paths, not procedural chronology; avoid turning architecture into
a step-by-step flowchart.

**GraphAgent routing**: `illustrator` (category: `agent_reasoning` or
`generative_learning`)

**Keywords**: 架构, 模块, 组件, 边界, 系统, architecture, module, component,
system, subsystem

---

### Technical Roadmap

**Use when reader needs to understand**: how study progresses, how one stage
leads to next, what each stage produces, where validation appears.

**Typical structure**: stage → key task → stage output.

**Quality focus**: keep progression visually directional and stage-based;
limit stage count to readable set; make outputs visible at end of each major
stage.

**GraphAgent routing**: `illustrator` (category: `science_applications`)

**Keywords**: 阶段, 路线图, 推进, roadmap, phase, milestone, stage, timeline

---

### Workflow / Process

**Use when reader needs to understand**: ordered steps, branching conditions,
loops, trigger and fallback logic.

**Typical nodes**: start, process, decision, fallback, output.

**Quality focus**: make branching explicit; label ambiguous decisions; keep
loops readable without tangled back-edges.

**GraphAgent routing**: `illustrator` (category: `science_applications`,
scenario: `flowchart`)

**Keywords**: 流程, 步骤, 分支, 循环, workflow, process, step, branch,
decision, loop, protocol

---

### Statistical Plot

**Use when reader needs to understand**: quantitative results, comparisons,
distributions, correlations.

**Typical forms**: bar chart, line chart, scatter plot, heatmap, violin plot,
UMAP, volcano plot.

**Quality focus**: data accuracy, colorblind-safe palette, appropriate chart
type for data structure.

**GraphAgent routing**: `data_plotter`

**Keywords**: 数据, 统计, 图表, bar, line, scatter, heatmap, violin, UMAP,
volcano, box plot, plot, chart

---

### Conceptual Framework

**Use when reader needs to understand**: theoretical relationships, abstract
concepts, high-level system logic without implementation detail.

**Typical forms**: mind map style, entity-relationship, ontology diagram.

**Quality focus**: abstract level is clear; no implementation detail leaking
into conceptual view.

**GraphAgent routing**: `illustrator` (category: `agent_reasoning`)

**Keywords**: 框架, 理论, 概念, 关系, framework, theory, concept, relationship,
ontology

---

### Scientific Schematic

**Use when reader needs to understand**: biological pathways, chemical reactions,
experimental protocols, cellular mechanisms.

**Typical forms**: signaling pathway, metabolic pathway, reaction scheme,
cellular schematic, mechanism cartoon.

**Quality focus**: biological/chemical accuracy; correct iconography conventions
(T-bars for inhibition, arrows for activation); compartment boundaries.

**GraphAgent routing**: `illustrator` (category: `science_applications`)

**Keywords**: 通路, 信号, 代谢, 细胞, 机制, pathway, signaling, metabolic,
cellular, mechanism, reaction, inhibition, activation

---

## Classification Logic

```
1. Scan S_source_context + C_communicative_intent + user message for keywords
2. Match to figure type using keyword table above
3. If ambiguous (≥2 types match) → run granularity_rule
4. Write result to brief.json:
   {
     "figure_type": "<type>",
     "routing": "<data_plotter | illustrator>",
     "category": "<statistical_plot | agent_reasoning | science_applications | ...>"
   }
```

## Output (add to brief.json)

```json
{
  "figure_type": "System Architecture",
  "routing": "illustrator",
  "category": "agent_reasoning",
  "classifier_confidence": "high",
  "classifier_reasoning": "User mentions '模块', '数据输入输出', '系统边界' — all architecture keywords. No temporal progression or execution branches."
}
```
