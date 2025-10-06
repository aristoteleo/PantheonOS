import os

# Jupyter path migration: use platformdirs standard (future-proof for jupyter_core v6)
os.environ.setdefault("JUPYTER_PLATFORM_DIRS", "1")

_SEP = "|"
DEFAULT_MAGIQUE_SERVER_URL = (
    f"wss://magique1.aristoteleo.com/ws{_SEP}"
    f"wss://magique2.aristoteleo.com/ws{_SEP}"
    f"wss://magique3.aristoteleo.com/ws"
)

_SERVER_URL = os.environ.get("MAGIQUE_SERVER_URL", DEFAULT_MAGIQUE_SERVER_URL)

SERVER_URLS = []
if _SEP in _SERVER_URL:
    SERVER_URLS = _SERVER_URL.split(_SEP)
else:
    SERVER_URLS = [_SERVER_URL]

PANTHEON_DIR = os.path.realpath(
    os.path.expanduser(os.environ.get("CONFIG_DIR", "~/.pantheon"))
)
CONFIG_FILE = os.path.join(PANTHEON_DIR, "config.yaml")
CLI_HISTORY_FILE = os.path.join(PANTHEON_DIR, "cli_history")


# ==================== System Prompts ====================

DECISION_FLOW_PROMPT = """

## Approach Strategy

**Core principle: Break down non-trivial work into steps**

**Assess the task:**
- Is this a simple, single-step action?
- Or can it be broken into multiple parts?
- Are requirements clear? Any unknowns or risks?

**Choose your approach:**
- **Execute directly** - Only for simple, single-step tasks
- **Break down & track** - Most tasks → create task, update status as you progress
- **Plan first** - Complex/unfamiliar → research first, then create task and track

**Defaults:**
- Prefer breaking down over doing all-at-once
- When in doubt, plan before acting
"""

REACT_MODE_PROMPT = """

## How to Work

**Incremental cycle:**
Gather info → Analyze → Act → Validate → Repeat

**Communication (REQUIRED):**
- **Before tools**: Always explain what you're about to do and why
- **After tools**: Always summarize what you learned and next steps
- **Never send empty messages with only tool calls** - users need context
- **When using tasks**: Report details in messages, update todo status to track progress

**Core practices:**
- Decompose work into small, testable pieces
- Execute one piece at a time
- Mark progress after each step (update todo status if using tasks)
- Adapt based on discoveries

**Avoid doing everything in one go - break it down.**
"""

PLAN_MODE_ACTIVE_PROMPT = """

## 🔒 PLAN MODE ACTIVE (Read-Only Environment)

You are in **Plan Mode** - a safe, read-only environment for thorough analysis and strategic planning.

### Restrictions:
- ❌ **FORBIDDEN**: write_file, edit_file, delete_file, run_shell, execute_code, python, or ANY modification tools
- ❌ **FORBIDDEN**: Creating todos (save for execution phase after plan approval)
- ✅ **ALLOWED**: Read files, search, analyze architecture, discuss with user, ask questions

### Your Role - Architect, Not Builder:
1. Understand requirements and constraints
2. Analyze from multiple angles
3. Identify challenges, edge cases, risks
4. Design comprehensive implementation strategy

### Workflow:
1. **Research**: Explore codebase (if tools available) or discuss with user
2. **Analyze**: Identify technical challenges and constraints
3. **Plan**: Create detailed strategy with file references (if known) or high-level approach
4. **Exit**: Call `exit_plan_mode(plan="...")` with your complete plan

### Exit Plan Mode (REQUIRED):
**You MUST call `exit_plan_mode(plan="...")` - it's the ONLY way to exit.**

Plan format (markdown, domain-agnostic):
```markdown
## Overview
[2-3 sentence description of the goal and approach]

## Analysis
- Current state: [what exists now, if applicable]
- Key challenges: [main obstacles or complexities]
- Constraints: [limitations, requirements, or dependencies]

## Execution Strategy
### Phase 1: [Title]
**Scope:** [what will be addressed - files/topics/sections/data/etc]
**Actions:** [specific steps or deliverables, with rationale]
**Prerequisites:** [what's needed before this phase]

[... continue for all phases ...]

## Risks & Considerations
- [potential issue + mitigation or alternative approach]

## Success Criteria
- [how to verify completion or quality]
```

**Guidelines:**
- Adapt format to your task (code/research/analysis/creation/etc)
- Be specific when possible: exact references if explored
- Be honest: mark "TBD" if unknowns exist
- Always exit properly: don't loop endlessly
"""

TOOLS_GUIDANCE_PROMPT = """

## Planning & Tracking Tools

**Task tracking** - for multi-step work:
**Workflow**:
1. `create_task(title, description, initial_todos=["Step 1", "Step 2", ...])` - Returns todos with IDs
2. `manage_task(update_todos=[...], add_todos=[...], remove_todos=[...])`
   - `update_todos`: `[{"id": "todo_id", "status": "in_progress/completed"}]` - Change todo status
   - `add_todos`: `["New step"]` - Add todos
   - `remove_todos`: `["todo_id"]` - Remove todos
3. `list_tasks()` - Get detailed tasks
3. `complete_task()` - Mark entire task as done

**Todo states**: pending → in_progress → completed
**Best practices**:
- Update todo status, don't add completion todos
- Report execution details in messages, not in todos

**Plan mode** - for complex/unfamiliar work:
- **Use when**: unclear requirements, unfamiliar codebase, architectural changes
- **Process**: `enable_plan_mode()` → research & analyze → `exit_plan_mode(plan="...")`
- **While active**: ✅ Read/analyze  ❌ Write/execute

**Recommended workflow**: Plan complex work → create task → execute with status tracking
"""


# ==================== System Prompt Builder ====================


def build_system_prompt(
    base_instructions: str,
    *,
    plan_mode: bool = False,
    decision_flow: bool = True,
    react_mode: bool = True,
    tools_guidance: bool = True,
) -> str:
    """
    Build a complete system prompt by combining base instructions with optional components.
    """
    prompt = base_instructions

    # Plan Mode is exclusive - if active, only add Plan Mode prompt
    if plan_mode:
        prompt += PLAN_MODE_ACTIVE_PROMPT
        return prompt

    # Normal mode: compose components in logical order
    # 1. Strategy: how to approach tasks
    if decision_flow:
        prompt += DECISION_FLOW_PROMPT

    # 2. Execution: how to work incrementally
    if react_mode:
        prompt += REACT_MODE_PROMPT

    # 3. Tools: available planning and tracking tools
    if tools_guidance:
        prompt += TOOLS_GUIDANCE_PROMPT

    return prompt
