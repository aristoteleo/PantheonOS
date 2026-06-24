---
id: skill-review
name: Skill Review
description: Evaluate a Pantheon store skill against a fixed rubric and emit a structured rating + review (per-dimension scores, an overall 0-100, a verdict, and best_for/not_for/caveats) for humans and for the auto-skill-selection mechanism. Use when assessing the quality of a store skill from its content. Do NOT use it to measure task lift — that requires the benchmark harness, not a read of the artifact.
tags: [meta, evaluation, store, quality, reviewer]
category: evaluation
---

# Skill Review

A standard for reviewing **agent skills** — the Markdown playbooks (`SKILL.md` + bundled
files) an LLM agent loads to do a task better. Apply this rubric to produce a consistent,
structured review that both humans and the store's auto-skill-selection mechanism can act on.

## Purpose & scope

This is the **cheap, scalable expert-review layer**. It judges the *artifact as written*:
is the content correct, actionable, well-scoped, safe, and routable? It is a **proxy** for
how useful a skill is — not a measurement of it.

It deliberately does **not** measure causal task lift (does an agent equipped with the skill
actually solve more tasks?). That requires running the skill against held-out tasks with a
deterministic verifier — the benchmark harness's job. When measured lift exists for a skill,
**trust it over this review**. Treat this rubric's score as the signal you use to decide
*which* skills are worth the expensive lift evaluation, and as a fallback where lift is absent.

You review from the text only — you cannot execute the skill. Be skeptical and discriminating:
most real skills land **2–4** on a dimension; reserve **5** for genuinely exemplary and **0–1**
for broken or misleading.

## The rubric (score each 0–5, with one line of evidence citing specifics)

| Dimension | Weight | 5 looks like | 0 looks like |
|---|---|---|---|
| **correctness** & currency | 25 | Tools/APIs/params/methods correct and current; code blocks look runnable | Deprecated APIs, wrong params, factually misleading |
| **activation** (when to use / not use) | 20 | Description gives precise positive AND negative triggers; a router can decide from it | Vague ("helps with bio analysis") or missing triggers |
| **actionability** | 20 | Concrete commands/code/exact params/decision rules; an agent follows it without guessing | Prose essay with no executable content |
| **completeness** & scope | 15 | Covers preconditions, edge cases, failure modes; scope matches the name | A stub, or scope absurdly broad/thin for the name |
| **safety** & robustness | 12 | Flags destructive/irreversible ops, tells the agent to validate results, handles errors | Runs dangerous ops with no checks or recovery |
| **efficiency** (signal density) | 8 | Tight, high-signal; every line earns the tokens it costs to load | Padded, repetitive, bloated |

Weights sum to 100. **Freshness is not in the rubric** — it is tracked objectively via the
package's `source_rev` / `source_committed_at`; cite it, don't score it subjectively.

## Computing overall & verdict (deterministic — do not eyeball it)

```
overall = round( sum( (score_dim / 5) * weight_dim  for each dimension ) )   # 0–100

verdict:
  correctness < 2  OR  overall < 50   -> not_recommended
  safety < 3       OR  overall < 70   -> use_with_caution
  else                                -> recommended
```

The reviewer model returns only the six scores + evidence + the structured fields below;
`overall` and `verdict` are computed from the scores so they never depend on the model doing
arithmetic or applying thresholds. A 1–5 star rating for display is `max(1, round(overall/20))`.

## Output schema

Return a single JSON object:

```json
{
  "scores": { "correctness": 0-5, "activation": 0-5, "actionability": 0-5,
              "completeness": 0-5, "safety": 0-5, "efficiency": 0-5 },
  "evidence": { "<dimension>": "one line citing specifics from the skill", ... },
  "summary": "2-4 sentence human-readable review",
  "best_for": ["tasks/contexts where this skill genuinely helps"],
  "not_for":  ["where it does not apply or is known to mismatch"],
  "caveats":  ["known issues/assumptions a caller must heed (e.g. hardcoded organism)"],
  "improvements": ["concrete fixes that would raise the score"],
  "confidence": "low | medium | high"
}
```

`best_for` / `not_for` / `caveats` are the **machine-consumable payload**: the auto-skill
mechanism filters by `verdict`, matches the current task against `best_for`/`not_for`, ranks
by `overall`, and surfaces `caveats` as load-time warnings — without parsing the prose.

## When to use / not use

- **Use** to: score store skills for quality/routing; gate ingestion; pick candidates for the
  expensive lift benchmark; feed `improvements` back into skill evolution.
- **Do not use** to: claim a skill improves task success (use the benchmark harness); judge a
  skill you can actually execute end-to-end (run it instead); rate non-skill content.

## Notes for the reviewer

- Penalize missing **negative triggers** ("when NOT to use / routes to X") — it is the single
  best discriminator of well-engineered skills and what the router most needs.
- If the harness clipped the content, it will say so inline — do **not** penalize completeness
  for the harness's clipping.
- Lower `confidence` when you could not verify correctness statically (e.g. external API
  behavior, exact column names) rather than guessing a high score.

> Canonical machine-readable form of this rubric lives in `pantheon/store/reviewer.py`
> (`RUBRIC`, `REVIEW_SCHEMA`, `compute_overall`), versioned as `RUBRIC_VERSION`. Keep them in sync.
