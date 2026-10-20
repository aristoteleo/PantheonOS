---
id: work_tracking
name: Work Tracking
description: File-based planning and todo management guidance
---

## Work Tracking with Files

Use file-based tracking for task management and planning. This provides transparency and persistence.

**File Structure:**
- Plan files live in `${{pantheon_dir}}/brain/${{client_id}}/<base_name>__plan.md` (letters/numbers/hyphens only)
- Todo files live in `${{pantheon_dir}}/brain/${{client_id}}/<base_name>__todo.md` 
- Create directories if they do not exist before writing

**When to Use:**
- **Plan file**: For complex tasks requiring research, design decisions, or when approach is unclear
- **Todo file**: For tracking concrete tasks, multi-step work, or when you need to show progress

**Workflow:**
1. Create/update the relevant file(s) at the start of non-trivial work
2. Mark tasks as in-progress when starting (`- [ ]` with inline note if helpful)
3. Update status as you complete work (`- [x]`) and log findings in the plan
4. Add newly discovered tasks immediately
5. Keep files updated to reflect current state so you can resume later without rereading history

**Best Practices:**
- Keep descriptions concise and actionable
- Update files before summarizing progress back to the user
- Leave completed work in place for auditability
- Use plan files to document decisions and rationale

**TODO.md Format:**
```markdown
---
kind: todo
title: <Title>
scope: Short description of what this list covers
---

# Task List

- [ ] Current task description
- [ ] Next task description (notes/blockers)
- [x] Finished task description (result summary)
```
- Checklist stays flat; checkbox (`[ ]` vs `[x]`) is the only state indicator.
- Reorder tasks freely; keep most relevant items near the top.
- Update the front matter if scope/title changes.

**PLAN.md Format:**
```markdown
---
kind: plan
title: <Title>
status: draft | in_progress | blocked | done
---

# Plan: [Goal/Feature Name]

## Overview
Brief description of what we're building/solving

## Approach
1. Step one
2. Step two
3. Step three

## Considerations
- Important notes
- Risks or dependencies

## Progress
- Current status and findings
```
- Update the `status` field whenever the plan state changes.
- Add extra sections (e.g., Metrics, Links) if needed, but keep the core sections.

**Usage Notes:**
- Plan and todo files are independent; create whichever provides value (or both).
- Mention the file paths you touched in your responses .
- If you skip creating these files for complex work, briefly justify why.
