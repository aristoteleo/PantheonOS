"""All prompt templates for the learning system."""

SKILLS_GUIDANCE = """\
## Skills (mandatory)

Before starting a task, scan the skills below. If a skill matches or is even \
partially relevant to your task, you MUST load it with `skill_view(name)` and \
follow its instructions. Err on the side of loading — it is always better to \
have context you don't need than to miss critical steps, pitfalls, or \
established workflows. Skills encode the user's preferred approach and \
conventions; load them even for tasks you already know how to do, because the \
skill defines how it should be done here.

<available_skills>
{skill_index}
</available_skills>

Only proceed without loading a skill if genuinely none are relevant to the task.

**Creating and Maintaining Skills:**

After completing a complex task (5+ tool calls), fixing a tricky error, \
or discovering a non-trivial workflow, consider whether this approach is worth \
saving for future reuse. If yes, create a skill with \
`skill_manage(action="create", name="...", content="...")`.

When using a skill and finding it outdated, incomplete, or wrong, \
patch it immediately with `skill_manage(action="patch", ...)` — don't wait \
to be asked. Skills that aren't maintained become liabilities.

**What belongs in skills:**
- Procedures: step-by-step workflows (HOW to do things)
- Trigger conditions: when to use this approach
- Pitfalls: common mistakes and how to avoid them
- Verification: how to confirm it worked

**What doesn't belong in skills:**
- Facts, preferences, context (use `.pantheon/memory-store/` instead)
- Simple one-offs that won't recur
- Abstract instructions without concrete examples

Skills are your procedural memory. Use them to capture proven approaches \
so you don't have to rediscover them each time."""


SKILL_EXTRACTION_SYSTEM = """\
You extract reusable procedural knowledge from conversations and save them as skills.

You have a limited turn budget. The efficient strategy is:
- Turn 1: Read session note (if session_note_path provided) AND existing skills in parallel
- Turn 2: Write new/updated skills (issue writes in parallel)

Use file_manager to read existing skills at .pantheon/skills/<name>/SKILL.md \
if you need to check before deciding to create or update. \
Write new or updated skills directly using file_manager.

**IMPORTANT**: When using glob or file_manager to access files in `.pantheon/`, \
use absolute paths. The `.pantheon/` directory is hidden and may be filtered \
by default glob behavior. The workspace root is provided in the context.

Skills support hierarchical organization. Use 'category/skill-name' paths \
(e.g. .pantheon/skills/bioinformatics/scrna-qc/SKILL.md) to group related skills. \
Flat names (e.g. .pantheon/skills/deploy-flyio/SKILL.md) are also fine.

Each skill file must have YAML frontmatter:
```
---
name: skill-name-here
description: One line description
tags: [optional, tags]
---

# Title

## When to Use
Trigger conditions for this skill.

## Procedure
1. Step one
2. Step two

## Pitfalls
- Common mistake to avoid

## Verification
- How to verify the procedure worked
```

Focus on:
1. Complex tasks that required 5+ steps or tool calls to complete
2. Non-trivial workflows discovered through trial and error
3. User corrections that revealed a preferred approach
4. Error recovery patterns that would help next time
5. Multi-step procedures that are likely to recur

Rules:
- Only save PROCEDURES, not facts or preferences (those belong in memory)
- Good skills have: trigger conditions, numbered steps, pitfalls, verification
- Skip simple one-offs that won't recur
- Keep skills focused on one workflow (not "everything about X")
- Include exact commands and concrete examples, not abstract instructions
- If an existing skill needs updating, read it first, then overwrite with improved content
- If session_note_path is provided, read it via file_manager in Turn 1 — it contains \
  structured workflow, files, and error context essential for writing accurate skills
- The session note may include a jsonl_path; read it only if the session note is insufficient

If nothing is worth saving, say so and do not create any files."""

SKILL_EXTRACTION_USER = """\
Review the conversation and identify reusable procedural knowledge worth \
saving as skills. You MUST only use content from the conversation and any \
files referenced below. Do not investigate or verify the content further.

Existing skills (do NOT duplicate these):
{skill_manifest}
{session_context}
Recent conversation:
{messages}"""
