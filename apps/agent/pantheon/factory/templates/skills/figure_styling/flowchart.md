# Flowchart Recipe

| Field | Value |
|---|---|
| UI outputType | `flowchart` |
| Primary rendering mode | AI-first |
| Best for | workflow diagrams, process diagrams, mechanisms, pipelines |
| Do not use for | data plots, statistical charts, contact maps |
| Read path | `SKILL.md` + this file |

---

## Visual goal

A clean scientific workflow diagram: clear process stages, unambiguous arrows, short labels, flat vector composition, journal or slide grade.

---

## Workflow schema

Use a clear scientific process structure:

| Stage | Meaning |
|---|---|
| Input | sample, material, dataset, or initial condition |
| Assay / acquisition | experiment, measurement, or data collection |
| Processing | sequencing, imaging, computation, or preprocessing |
| Feature extraction | called peaks, domains, clusters, events, or measurements |
| Integration | combining evidence layers or modalities |
| Interpretation | biological, clinical, computational, or engineering conclusion |

Not every workflow needs all stages — use only what the user's process contains.

---

## Arrow semantics

- **Solid arrow** = main process flow
- **Dashed arrow** = optional integration or supporting evidence
- **Curved arrow** = iteration or feedback, only when explicitly requested
- Do not use inhibition arrows unless the prompt asks for repression

---

## Visual Schema

| Zone | Content |
|---|---|
| Top / left entry | input materials or initial conditions |
| Middle pathway | main process steps in order |
| Branch nodes | parallel or optional sub-processes |
| Right / bottom exit | outputs, results, or biological conclusion |

Use spatial grouping to show parallel processes. Use indentation or column alignment to show hierarchical steps.

---

## Structured prompt scaffold

```
[STYLE]
Flat vector scientific workflow diagram, clean white background, restrained palette, editable-looking nodes, journal-grade clarity.

[LAYOUT]
Describe the stage sequence and any branches.

[NODES]
List concrete process steps with short labels.

[ARROWS]
Describe connections: solid flow, dashed evidence branch, or iteration loop.

[LABELS]
Short labels only — <= 8 words per node.

[NEGATIVE]
No decorative icons unrelated to scientific steps, no 3D glow, no artistic illustration, no fake data panels.
```

---

## Node style rules

| Node type | Shape |
|---|---|
| Process step | rectangle |
| Decision point | diamond |
| Input / output | rounded rectangle |
| Sub-process group | dashed container rectangle |

Use a consistent color scheme:
- Node background: light gray or white
- Active / key step: accent color (single hue, e.g. #3A7BD5)
- Text: dark gray, short, readable at 9–10pt

---

## Label rules

- <= 8 words per node
- Use domain-specific step names, not generic placeholders
- No abbreviations unless universally standard in the field
- Short verb + noun: "Extract DNA", "Align Reads", "Call Peaks"

---

## Negative constraints

Avoid:
- Decorative icons unrelated to the scientific process
- Generic AI art or clipart
- 3D nodes or shadows
- Dense paragraph text inside nodes
- Inconsistent node sizes
- Ambiguous arrows (missing arrowheads)
- Abstract boxes without scientific meaning
- More than 12 nodes without grouping

---

## Domain-specific example: 3D genomics workflow

For 3D genomics, the process typically follows:

```
Input (cells / tissue)
→ Hi-C / Micro-C / Capture-C library preparation
→ Sequencing
→ Contact map generation
→ TAD / loop / compartment calling
→ Integration with ATAC-seq / ChIP-seq / RNA-seq
→ Biological interpretation
```

Node labels should use domain vocabulary: "Hi-C Library Prep", "Contact Map", "TAD Calling", "Loop Anchors", "Compartment A/B", "Enhancer–Promoter Interaction".

This is a domain example only — apply the same schema to any scientific workflow.

---

## Quick self-check

- [ ] Clear stage sequence
- [ ] Arrow types match semantics (solid / dashed / curved)
- [ ] Node labels <= 8 words
- [ ] No decorative 3D or clipart
- [ ] Consistent node shapes and colors
- [ ] Single clear entry and exit
- [ ] Grouped parallel steps if needed
