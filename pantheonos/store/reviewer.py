"""Skill reviewer: a versioned rubric + a static (read-only) LLM reviewer that
scores a store skill and emits a structured review for both humans and the
downstream auto-skill-selection mechanism.

This is the cheap, scalable "expert review" layer. It judges the *artifact*
(SKILL.md + bundled files + metadata), NOT causal task lift — that needs the
benchmark harness. When measured lift exists, trust it over this score.

Design notes:
- The model returns only the 6 dimension scores (0-5) + evidence + the
  structured routing fields. `overall` (0-100) and `verdict` are computed
  deterministically from the scores here, so they don't depend on the model
  doing arithmetic or applying thresholds consistently.
- RUBRIC_VERSION lets us re-review when the standard itself changes (separate
  from re-reviewing when a skill's content_hash/version changes).
"""

RUBRIC_VERSION = "1.0.0"

# (key, label, weight, what 5 looks like, what 0 looks like)
RUBRIC = [
    ("correctness", "Correctness & currency", 25,
     "Tools/APIs/params/methods are correct and current; code blocks look runnable.",
     "Deprecated APIs, wrong params, or factually misleading guidance."),
    ("activation", "Activation (when to use / not use)", 20,
     "Description gives precise positive AND negative triggers; a router can decide from it.",
     "Vague ('helps with bio analysis') or missing trigger guidance."),
    ("actionability", "Actionability", 20,
     "Concrete commands/code/exact params/decision rules; an agent can follow it without guessing.",
     "Prose essay with no executable content."),
    ("completeness", "Completeness & scope", 15,
     "Covers preconditions, edge cases, failure modes; scope matches the name.",
     "A stub, or scope absurdly broad/thin for the name."),
    ("safety", "Safety & robustness", 12,
     "Flags destructive/irreversible ops, tells the agent to validate results, handles errors.",
     "Runs dangerous operations with no checks or recovery."),
    ("efficiency", "Efficiency (signal density)", 8,
     "Tight and high-signal; every line earns the tokens it costs to load.",
     "Padded, repetitive, or bloated."),
]
WEIGHTS = {k: w for k, _, w, _, _ in RUBRIC}  # sums to 100

# Model-facing output schema (overall/verdict are computed, NOT requested here).
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {k: {"type": "integer", "minimum": 0, "maximum": 5} for k, *_ in RUBRIC},
            "required": [k for k, *_ in RUBRIC],
            "additionalProperties": False,
        },
        "evidence": {
            "type": "object",
            "description": "One short justification per dimension, citing specifics from the skill.",
            "properties": {k: {"type": "string"} for k, *_ in RUBRIC},
            "additionalProperties": False,
        },
        "summary": {"type": "string", "description": "2-4 sentence human-readable review (becomes the review comment)."},
        "best_for": {"type": "array", "items": {"type": "string"},
                     "description": "Tasks/contexts where this skill genuinely helps."},
        "not_for": {"type": "array", "items": {"type": "string"},
                    "description": "Where it does not apply or is known to mismatch."},
        "caveats": {"type": "array", "items": {"type": "string"},
                    "description": "Known issues/assumptions a caller must heed (e.g. hardcoded organism)."},
        "improvements": {"type": "array", "items": {"type": "string"},
                         "description": "Concrete fixes that would raise the score."},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"],
                       "description": "Reviewer's confidence; lower it when correctness couldn't be verified statically."},
    },
    "required": ["scores", "summary", "best_for", "not_for", "caveats", "confidence"],
    "additionalProperties": False,
}


def compute_overall(scores: dict) -> tuple:
    """Deterministically fold the 6 dimension scores into (overall 0-100, verdict).

    Verdict gates (not just the weighted sum):
      - correctness < 2  OR  overall < 50  -> not_recommended
      - safety < 3       OR  overall < 70  -> use_with_caution
      - else                               -> recommended
    """
    overall = round(sum((scores.get(k, 0) / 5) * w for k, w in WEIGHTS.items()))
    if scores.get("correctness", 0) < 2 or overall < 50:
        verdict = "not_recommended"
    elif scores.get("safety", 0) < 3 or overall < 70:
        verdict = "use_with_caution"
    else:
        verdict = "recommended"
    return overall, verdict


def _rubric_block() -> str:
    return "\n".join(
        f"- **{k}** ({label}, weight {w}): 5 = {hi}  |  0 = {lo}"
        for k, label, w, hi, lo in RUBRIC
    )


REVIEWER_SYSTEM = """You are a skeptical senior reviewer of *agent skills* — Markdown playbooks an LLM agent loads to do a task better. You judge the artifact as written (you cannot run it). Be discriminating: most real skills land 2-4 on a dimension; reserve 5 for genuinely exemplary and 0-1 for broken/misleading. Reward concrete, executable, correctly-scoped guidance; penalize vague prose, deprecated/wrong calls, missing trigger conditions, unflagged destructive operations, and bloat. Your scores feed an automatic skill-selection mechanism, so your best_for / not_for / caveats must be accurate and specific enough to route on."""


def build_review_prompt(skill: dict) -> str:
    """skill: {name, display_name, description, category, source, source_url,
    references, content, files: {path: content}}."""
    files = skill.get("files") or {}

    def _clip(text: str, limit: int) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        # Mark our own clipping so the reviewer doesn't penalize completeness for it.
        return text[:limit] + f"\n\n[... reviewer note: {len(text) - limit} chars clipped HERE by the harness, NOT missing from the skill ...]"

    file_blob = "\n\n".join(
        f"### bundled file: {p}\n```\n{_clip(c, 16000)}\n```" for p, c in list(files.items())[:12]
    ) or "(none)"
    refs = ", ".join(skill.get("references") or []) or "(none)"
    return f"""Review this store skill against the rubric. Score each dimension 0-5 with one line of evidence citing specifics.

## Rubric
{_rubric_block()}

## Skill metadata
- store name: {skill.get('name')}
- display: {skill.get('display_name')}
- category: {skill.get('category')}
- source: {skill.get('source')} ({skill.get('source_url')})
- declared references: {refs}
- frontmatter description: {skill.get('description')}

## SKILL.md
```
{_clip(skill.get('content') or '', 60000)}
```

## Bundled files
{file_blob}

Return ONLY the structured review. overall/verdict are computed downstream — do not include them."""
