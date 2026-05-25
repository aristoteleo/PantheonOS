---
id: paper_writing_skill_structure_check
name: Skill Structure Check
description: Audit paper-writing skill files for trigger descriptions, routing pipeline, entry/exit criteria, one-hop references, and pressure scenarios.
tags: [paper_writing, skill_design, quality]
---

# Skill Structure Check

Use when changing this skill family.

## Checklist

- Root `description` includes real trigger terms.
- Root skill uses Routing + Sequential Pipeline.
- Workflow files specify entry criteria, actions, and exit criteria.
- References are one hop from `SKILL.md`.
- Each `SKILL.md` stays below 500 lines.
- Long templates and examples live in adjacent files.
- At least three pressure scenarios compare baseline behavior with skill-guided
  behavior.

Sources: Anthropic skill-creator/SKILL.md, Trail of Bits
designing-workflow-skills/SKILL.md, obra writing-skills/SKILL.md.
