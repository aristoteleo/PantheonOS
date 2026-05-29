---
id: conference_talk_scenario
name: Conference Talk Scenario
description: |
  Workflow for transforming a paper or research project into a conference
  talk: distill a core message, build a storyline arc, design slide structure,
  and produce speaker notes.
tags: [paper_writing, talk, presentation, conference]
---

# Conference Talk Scenario

Use when the user needs to convert existing research or a written paper into a
spoken conference talk (typically 10–20 minutes plus Q&A).

## Contract

| Field | Value |
|---|---|
| Trigger | "会议演讲", "conference talk", "presentation", "oral", "演讲", "talk" |
| Inputs | a paper draft or research summary, figures, target talk length, audience type, venue |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (Figure Storyline + Reader Testing sections), [../writing/SKILL.md](../writing/SKILL.md) |
| Outputs | `{workdir}/core_message.md`, `{workdir}/storyline.md`, `{workdir}/draft/slide_outline.md`, `{workdir}/draft/speaker_notes.md`, `{workdir}/report/<slug>_talk.html` |
| Format | `conference_talk` |
| Theme | `kami_academic` (or a slide theme set by the venue) |
| Gates | storyline coherence review, `format_lint`, `html_editability_check` |
| Forbidden | dumping paper sections onto slides; presenting without speaker notes; using figures without a spoken takeaway |

## Section Structure (15-minute talk arc)

| Beat | Time | Purpose |
|---|---|---|
| Hook | 30s | grab attention; concrete image or surprise |
| Problem | 2m | why the question matters |
| Insight | 1m | the key realization that unlocks the work |
| Solution | 3m | what was built / done |
| Evidence | 4m | main results, ≤ 3 key figures |
| Impact | 1m | what this enables |
| Takeaway | 30s | reinforce the core message; actionable next step (link/code) |
| Q&A buffer | remaining | optional |

Adjust beat times for 10-min, 20-min, or keynote slots; keep the seven-beat
arc unless the venue mandates a different structure.

## Default Path

```text
distill_core_message → build_storyline → figure_storyline (select ≤3 hero figures)
  → slide_outline → speaker_notes → reader_testing (mock audience pass)
  → editable HTML preview → finalize_packet
```

## Scenario-Specific Rules

- **One slide, one point**. No "and also" slides.
- **Every figure has a spoken takeaway**. If a figure cannot be summarized in
  one sentence, it does not belong in the talk.
- **Speaker notes are mandatory**, not optional. Each slide gets a notes block
  with what to say, transitions, and time budget.
- **Story arc, not paper sections**. The talk is structured by beats above,
  not by IMRaD. Methods detail collapses into Solution; Discussion collapses
  into Impact + Takeaway.
- **Backup slides** for likely Q&A topics (statistical significance, limitations,
  related work comparisons) live after the takeaway slide, not in the main arc.
- **Code/data links** appear on the takeaway slide; do not bury them.

## Slide Budget Heuristic

- 10–12 slides for a 15-minute talk (about 1–1.5 minutes per slide).
- Add 3–6 backup slides for Q&A.
- Slide count for other lengths: roughly `talk_minutes` ± 2.

## Customization

- **Workshop talk**: use [workshop_share.md](./workshop_share.md) instead — the
  pedagogy structure is different.
- **Keynote / invited talk**: extend Hook to 1m and Impact to 2–3m; tell more
  story, show fewer numbers.
- **Lightning talk (≤5m)**: collapse to Hook → Insight → Solution → Takeaway
  only; keep ≤ 1 hero figure.

## Success Metrics

- A core message stated in one sentence.
- Storyline file follows the seven-beat arc.
- Each slide has speaker notes and a time budget.
- Each figure used has a one-sentence spoken takeaway.
- Practice-run reading time within ±10% of target talk length.

Sources: PR 104 conference_talk distillation, AI-Scientist talk-shaping notes.
