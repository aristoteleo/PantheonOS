---
category: learning
description: |
  Team for learning from any data source and managing the skillbook.
  Skill Manager orchestrates reflector and developer, makes decisions,
  and updates skillbook via SkillbookToolSet.
icon: 🧠
id: skill_learning_team
name: Skill Learning Team
type: team
version: 4.1.0
agents:
  - skill_learning_v1/skill_manager
  - skill_learning_v1/developer
  - skill_learning_v1/reflector
---

# Skill Learning Team

A 3-agent team for extracting knowledge from any source into a skillbook.

## Agent Roles

| Agent | Icon | Role | Tools |
|-------|------|------|-------|
| **skill_manager** | 📚 | Leader - orchestrate, decide, update | skillbook |
| **reflector** | 🔍 | Analyze sources, extract learnings | file_manager |
| **developer** | 🛠️ | Fetch, validate, execute | file_manager, shell, python, browser |

---

## Architecture

```
              ┌─────────────────┐
              │  Skill Manager  │
              │    (Leader)     │
              │                 │
              │ compress_trajectory()
              │ add/update/tag_skill()
              │ call_agent()    │
              └────────┬────────┘
                       │
         ┌─────────────┴─────────────┐
         │                           │
         ▼                           ▼
  ┌─────────────┐            ┌─────────────┐
  │  Reflector  │            │  Developer  │
  │             │            │             │
  │ read file   │            │ fetch URL   │
  │ extract     │            │ validate    │
  │ create /tmp │            │ run code    │
  └─────────────┘            └─────────────┘
```

---

## Workflow

### Memory/Conversation → Skills

```
1. skill_manager: compress_trajectory(memory_path)
   → trajectory_path + details_path

2. skill_manager: call reflector("Analyze: ...")
   
3. reflector: read trajectory (compressed)
   → [if needed] read details (full)
   → extract learnings
   → create /tmp files for complex skills
   → return JSON

4. skill_manager: process learnings
   → list_skills() check duplicates
   → add_skill() / update_skill()
   → tag_skill() for skill_tags
```

### External Source (URL/PDF/Repo) → Skills

```
1. skill_manager: call developer("Fetch: {url}")
   → content

2. skill_manager: call reflector("Analyze: {content}")
   → extracted_learnings

3. skill_manager: process learnings (same as above)
```

---

## Shared Resources

| Resource | Managed By | Notes |
|----------|------------|-------|
| `skillbook.json` | SkillbookToolSet | System skills + ratings |
| `skills/*.md` | SkillbookToolSet | Copies complex skills from /tmp |
| `SKILLS.md` | User | User-defined, NOT modified by team |

---

## Key Points

1. **skill_manager** uses `skillbook` toolset (not file_manager)
2. **reflector** creates files in `/tmp/skills_xxx/`
3. **skill_manager** copies to `skills/` via `add_skill(sources=...)`
4. **SKILLS.md** is user-managed, never modified by this team
