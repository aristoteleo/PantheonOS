---
category: learning
description: |-
  Team V3 for skill learning - Multi-agent architecture with Pipeline prompts.
  Supports both conversation and document learning.
  Coordinator orchestrates workflow, Reflector/Executor/SkillManager use exact Pipeline prompts.
icon: 🧠
id: skill_learning_team_v3
name: Skill Learning Team V3
type: team
version: 2.0.0
agents:
  - skill_learning_v3/coordinator
  - skill_learning_v3/reflector
  - skill_learning_v3/executor
  - skill_learning_v3/extractor
  - skill_learning_v3/skill_manager
---

# Skill Learning Team V3

A multi-agent team that supports both conversation and document learning.

## Architecture

```
              ┌─────────────────────┐
              │     Coordinator     │  ← Has tools (skillbook, file_manager)
              │  (workflow + tools) │
              └──────────┬──────────┘
                         │
          ┌──────────────┼──────────────┐
          ↓              ↓              ↓
┌───────────────┐  ┌──────────┐  ┌───────────────┐
│   Reflector   │  │ Executor │  │ Skill Manager │
│ (trajectory)  │  │(document)│  │  (decisions)  │
│  No tools     │  │   ↓      │  │  No tools     │
│  Returns JSON │  │Extractor │  │  Returns JSON │
└───────────────┘  └──────────┘  └───────────────┘
```

## Agent Roles

| Agent | Role | Tools |
|-------|------|-------|
| **coordinator** | Orchestrate workflow, call sub-agents, apply operations | skillbook, file_manager |
| **reflector** | Analyze conversation trajectory, extract learnings | file_manager |
| **executor** | Process documents, coordinate chunk analysis | file_manager |
| **extractor** | Extract skills from documents | file_manager |
| **skill_manager** | Query skillbook, decide update operations | skillbook, file_manager |

## Workflows

### Conversation Learning

```
1. Coordinator: compress_trajectory(memory_path)
2. Coordinator: call_agent("reflector", trajectory_content)
3. Coordinator: apply skill_tags via tag_skill()
4. Coordinator: call_agent("skill_manager", learnings)
5. Coordinator: apply operations via add_skill/update_skill/...
6. Coordinator: report summary
```

### Document Learning (NEW)

```
1. Coordinator: call_agent("executor", document_path)
   └─→ Executor: read_file, split chunks
       └─→ FOR EACH chunk: call_agent("extractor", chunk)
       └─→ Consolidate learnings
2. Coordinator: call_agent("skill_manager", learnings)
3. Coordinator: apply operations via add_skill/update_skill/...
4. Coordinator: report summary
```

## Key Features

- **Dual learning modes** - Conversation (via Reflector) and Document (via Executor)
- **Coordinator handles flow** - Only orchestration, no analysis
- **Auto-save** - Each skillbook operation saves automatically
- **User-defined protection** - Cannot modify user-defined skills
