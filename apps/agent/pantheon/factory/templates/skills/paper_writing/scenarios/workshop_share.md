---
id: workshop_share_scenario
name: Workshop Share Scenario
description: |
  Workflow for designing a tutorial / workshop session: define learning
  objectives, choose a tutorial structure, produce step-by-step instructions
  with runnable code examples, and prepare troubleshooting + Q&A material.
tags: [paper_writing, workshop, tutorial, hands_on, training]
---

# Workshop Share Scenario

Use when the user needs to teach others how to use a method, library, or
analysis pipeline through a hands-on tutorial. Different from a conference
talk — the goal is participant skill, not narrative impact.

## Contract

| Field | Value |
|---|---|
| Trigger | "workshop", "tutorial", "教学", "hands-on", "training", "上手指南" |
| Inputs | the method/tool to teach, target audience, duration, prerequisites, sample data or repo |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (Material Inventory + Reader Testing sections), [../writing/SKILL.md](../writing/SKILL.md) |
| Outputs | `{workdir}/learning_objectives.md`, `{workdir}/draft/tutorial.md`, `{workdir}/draft/code_examples/`, `{workdir}/draft/troubleshooting.md`, `{workdir}/report/<slug>_workshop.html` |
| Format | `workshop_share` |
| Theme | `kami_academic` (or a theme matched to the platform) |
| Gates | reader_testing pass, `format_lint`, `html_editability_check`, code-runs-on-clean-env check |
| Forbidden | tutorials that cannot be reproduced from a clean environment; code that depends on undocumented setup; skipping expected outputs |

## Section Structure

| Section | Required | Notes |
|---|---|---|
| Learning Objectives | yes | "By the end, participants will be able to…" — verbs are action-oriented |
| Prerequisites | yes | runtime versions, prior knowledge, accounts |
| Materials | yes | data files, repo links, scaffold notebooks |
| Steps | yes | numbered, with code block + expected output for every step |
| Checkpoints | yes | "Stop here and verify X" anchors throughout |
| Expected Outputs | yes | what participants should see at the end |
| Troubleshooting | yes | common pitfalls with fixes |
| Q&A | optional | anticipated questions with worked answers |

Each step block follows: **what to do → code/command → expected output → why
it matters**.

## Three Common Tutorial Shapes

| Shape | Best for | Outline |
|---|---|---|
| Linear workflow | step-by-step pipeline | Setup → Load → Preprocess → Run → Evaluate → Visualize → Troubleshoot |
| Problem-based | hooking motivated learners | Problem → Solution overview → Hands-on fix → Advanced tuning → Real-world example |
| Modular | self-paced or mixed audiences | Quick Start (15m) → Detailed Walkthrough (45m) → Advanced (30m) → FAQ (30m) |

## Default Path

```text
define_learning_objectives → choose_structure → draft tutorial.md
  → produce code_examples/ (runnable from clean env)
  → reader_testing (mock participant pass) → troubleshooting.md
  → format_lint → editable HTML → finalize_packet
```

## Scenario-Specific Rules

- **Reproducible from a clean environment**. The tutorial must run end-to-end
  on a clean machine following only the documented prerequisites. If it can't,
  the prerequisites list is wrong.
- **Every step has expected output**. Participants need to know whether they
  succeeded; missing expected output is the #1 cause of stuck learners.
- **Second person, imperative voice**. "Run this", "you will see", not "we run"
  / "the user runs".
- **Explain WHY, not just HOW**. Each conceptual step gets a one-paragraph
  rationale.
- **Checkpoints every 15–20 minutes**. Force a pause to verify state.
- **Time budget per section**. Print expected duration; over-running is the
  workshop equivalent of over-claiming.

## Customization

- **1-hour quick session**: collapse to Quick Start + 1 Hands-on + Troubleshooting.
- **Half-day**: linear workflow with 2 checkpoints; ~3 hands-on exercises.
- **Full-day**: modular shape with break-aware section budgeting; include
  optional advanced track.
- **Async / self-paced**: add a "verify your output" snippet after each step
  that participants can run to self-check.

## Success Metrics

- Tutorial runs end-to-end on a clean environment in the documented duration.
- Each step shows expected output.
- Reader testing pass identifies no stuck points or missing prerequisites.
- Troubleshooting covers the top 5 likely failure modes.
- HTML output meets the editable-block contract.

Sources: PR 104 workshop_share distillation.
