# Pantheon Memory & Learning Systems - Technical Documentation

> This document provides a comprehensive overview of the two subsystems for reviewers, covering mechanisms, data flows, and Pantheon integration.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Storage Layout](#2-storage-layout)
3. [Memory Types](#3-memory-types)
4. [Memory File Format](#4-memory-file-format)
   - 4.1 [Durable Memory Format](#41-durable-memory-format)
   - 4.2 [Skill Format](#42-skill-format)
   - 4.3 [Session Notes Format](#43-session-notes-format-retrievable)
5. [Memory System Core Flows](#5-memory-system-core-flows-pseudocode)
   - 5.1 [Session Start](#51-session-start)
   - 5.2 [Run Start — Memory Retrieval](#52-run-start--memory-retrieval-with-session-notes)
   - 5.3 [Agent Memory Operations](#53-agent-memory-operations)
   - 5.4 [Run End — Three-Step Post-Processing](#54-run-end--three-step-post-processing)
   - 5.5 [Compression Trigger — Session Memory Compact](#55-compression-trigger--session-memory-compact)
6. [Learning System Core Flows](#6-learning-system-core-flows-pseudocode)
   - 6.1 [Session Start — Skill Index Injection](#61-session-start--skill-index-injection)
   - 6.2 [Agent Skill Operations](#62-agent-skill-operations)
   - 6.3 [Run End — Counter Increment](#63-run-end--counter-increment)
7. [Pantheon Integration](#7-pantheon-integration)
   - 7.1 [Architecture](#71-architecture)
   - 7.2 [Plugin Lifecycle](#72-plugin-lifecycle)
   - 7.3 [Singleton Pattern](#73-singleton-pattern)
   - 7.4 [ChatRoom Integration](#74-chatroom-integration)
   - 7.5 [Configuration](#75-configuration-settingsjson)
8. [Lifecycle Timeline](#8-lifecycle-timeline)
9. [Code Scale](#9-code-scale)
10. [Key Design Decisions](#10-key-design-decisions)
    - 10.1 [Concurrency Model](#101-concurrency-model)
    - 10.2 [Counter Accuracy](#102-counter-accuracy)
    - 10.3 [Cursor Snapshot](#103-cursor-snapshot)
    - 10.4 [Skills Guidance Alignment](#104-skills-guidance-alignment)
    - 10.5 [Session Notes Retrieval Design](#105-session-notes-retrieval-design)
11. [Testing Coverage](#11-testing-coverage)
12. [Complete Data Flow Example](#12-complete-data-flow-example-cross-session-continuity)
13. [LLM API Call Analysis](#13-llm-api-call-analysis)
14. [Simplified Flow Diagram](#14-simplified-flow-diagram)

---

## 1. System Overview

Pantheon has two independent but parallel cross-session knowledge systems:

| Dimension | Memory System | Learning System |
|-----------|--------------|-----------------|
| **Purpose** | Declarative knowledge — "what to know" | Procedural knowledge — "how to do" |
| **Storage** | `.pantheon/memory-store/*.md` + session notes | `.pantheon/skills/<name>/SKILL.md` |
| **Index** | `.pantheon/MEMORY.md` (200 line limit) | Skill list injected in system prompt |
| **Retrieval** | LLM semantic selection (≤15 memories + ≤5 chats) | Agent loads on-demand via `skill_view()` |
| **Auto-extraction** | Every turn (Extract Memories) | Every N runs (Skill Extractor) |
| **Consolidation** | Dream 4-phase background agent multi-turn | No auto-consolidation (skills are self-contained) |
| **Agent tools** | 0 (file_manager + prompt guidance) | 3 `skill_*` tools |
| **Toggle** | `memory_system.enabled` | `learning_system.enabled` |

Both systems are fully decoupled, sharing `TeamPlugin` lifecycle but no code or storage.

---

## 2. Storage Layout

```
<workspace>/.pantheon/
│
├── MEMORY.md                          ← Memory index (200 lines / 25KB limit)
│     Format: "- [Title](memory-store/file.md) — summary"
│
├── memory-store/                      ← Persistent memories (declarative knowledge)
│   ├── user_senior_go_engineer.md     ← One file per memory (frontmatter + body)
│   ├── feedback_testing_policy.md
│   ├── workflow_scrna_qc.md
│   ├── logs/                          ← Daily logs (memory_write entry point)
│   │   └── 2026/04/2026-04-10.md
│   └── .consolidate-lock              ← Dream PID lock
│
├── skills/                            ← Skill library (procedural knowledge)
│   ├── deploy-flyio/
│   │   ├── SKILL.md                   ← YAML frontmatter + Markdown steps
│   │   └── scripts/deploy.sh          ← Supporting files
│   ├── scrna-qc-pipeline/
│   │   ├── SKILL.md
│   │   └── references/thresholds.md
│   └── debug-oom/
│       └── SKILL.md
│
├── memory-runtime/                    ← Runtime state
│   ├── session-notes/<id>.md          ← Session notes with YAML frontmatter (retrievable)
│   └── session-memory/<id>.md         ← Legacy alias (same as session-notes)
│
└── skills-runtime/                    ← Learning system runtime (reserved)
```

---

## 3. Memory System Data Model

### 3.1 Five-Type Taxonomy

```python
class MemoryType(str, Enum):
    USER      = "user"       # User role, skills, preferences
    FEEDBACK  = "feedback"   # Behavioral guidance (do/avoid, with Why + How to apply)
    PROJECT   = "project"    # Project context, decisions, deadlines
    REFERENCE = "reference"  # External system pointers
    WORKFLOW  = "workflow"   # Reusable procedures, troubleshooting steps
```

### 3.2 File Format (Phase 1 Schema)

```yaml
---
id: feedback-use-real-databases
title: Testing policy—use real databases
type: feedback
summary: Integration tests must hit real DB, not mocks
---

Do not mock databases in tests.

**Why:** Last quarter mocks passed but production migration failed.
**How to apply:** Use real databases when writing integration tests.
```

4 canonical fields: `id`, `title`, `type`, `summary`. Backward compatible with `name` → `title`, `description` → `summary`.

---

## 4. Learning System Data Model

### 4.1 SKILL.md Format

```yaml
---
name: scrna-qc-pipeline                   # Required, ^[a-z0-9][a-z0-9._-]*$, ≤64 chars
description: Single-cell RNA-seq QC pipeline  # Required, ≤1024 chars
version: 1.0.0                             # Optional
tags: [bioinformatics, qc]                 # Optional
related_skills: [doublet-detection]        # Optional
agent_scope: [researcher]                  # Optional (null = all agents)
---

# scRNA-seq QC Pipeline

## When to Use
Trigger when mitochondrial gene ratio is abnormally high (>20%).

## Procedure
1. Calculate mito ratio
2. Set threshold (default 20%, brain tissue 30%)
3. Filter and verify

## Pitfalls
- Different tissues have different thresholds

## Verification
- Retain 80-95% of cells
```

### 4.2 Validation Rules

| Check | Rule |
|-------|------|
| Name | `^[a-z0-9][a-z0-9._-]*$`, ≤64 chars |
| Description | ≤1,024 chars |
| Content | ≤100,000 chars |
| Supporting files | ≤1 MiB, only `references/scripts/templates/assets/` allowed |
| Security scan | Regex detection for prompt injection (6 patterns) |
| Collision detection | Check entire directory for duplicate names before creation |

---

## 4.3 Session Notes Format (Retrievable)

Session notes are automatically maintained summaries of ongoing conversations, now retrievable for cross-session continuity.

### Format

```yaml
---
title: Task: Analyze scRNA-seq QC pipeline
summary: Investigating high mitochondrial ratio (>25%), adjusting QC thresholds for brain tissue
type: session_note
session_id: chat-1
updated: 2026-04-15T10:30:00Z
last_message_index: 42
---

## Task State
Currently analyzing single-cell RNA-seq data. Discovered abnormally high
mitochondrial gene ratio (>25%) in 30% of cells.

## Files
- data/scrna_raw.h5ad - Raw count matrix
- scripts/qc_analysis.py - QC pipeline script

## Workflow
1. Loaded raw data (15,000 cells, 20,000 genes)
2. Calculated QC metrics (mito ratio, gene counts, UMI counts)
3. Identified high mito ratio issue

## Errors
- Initial threshold of 20% too strict for brain tissue
- Solution: Literature suggests 25-30% for brain samples

## Learnings
- Brain tissue naturally has higher mitochondrial content
- Need tissue-specific QC thresholds, not universal defaults

## Next Steps
1. Adjust mito threshold to 28%
2. Re-run filtering
3. Verify cell retention rate (target: 80-95%)

## Context
This is part of a larger project analyzing neuronal cell types.

## Decisions
- Use 28% mito threshold (based on literature review)
- Keep gene count threshold at 200 (standard)

## References
- Paper: "Brain scRNA-seq QC guidelines" (Nature Methods 2023)
```

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | ✅ | Brief title (max 100 chars), LLM-generated |
| `summary` | string | ✅ | One-sentence summary (max 200 chars), LLM-generated |
| `type` | string | ✅ | Fixed value "session_note" |
| `session_id` | string | ✅ | Chat/session identifier |
| `updated` | ISO8601 | ✅ | Last update timestamp |
| `last_message_index` | int | ✅ | System-managed, for compression boundary |

### Generation

- **LLM generates**: `title` and `summary` (high quality, semantic understanding)
- **System overrides**: `type`, `session_id`, `updated`, `last_message_index`
- **Fallback**: If LLM format fails, system extracts title from first heading and summary from Task State section

### Retrieval

- **Scanned**: Recent 10 session notes (configurable)
- **Indexed**: Via frontmatter `summary` field (fast scan, no full content read)
- **Selected**: Up to 3 chats per query (separate from memory limit)

---

## 5. Memory System Core Flows (Pseudocode)

### 5.1 Session Start

```
on_team_created(team):
    index_content = store.read_index()
    pantheon_dir = get_settings().pantheon_dir
    guidance = MEMORY_GUIDANCE.replace(".pantheon/", f"{pantheon_dir}/")
    section = guidance
    if index_content:
        section += "\n\n### Current Memory Index\n\n" + index_content
    for agent in team.agents:
        agent.instructions += section
```

### 5.2 Run Start — Memory Retrieval (with Session Notes)

```
on_run_start(team, user_input, context):
    query = str(user_input)

    // Step 1: Scan memory-store + recent session notes
    memory_headers = store.scan_headers()  // ≤200 entries, mtime desc
    session_headers = []
    if runtime_dir:
        session_notes_dir = runtime_dir / "session-notes"
        notes = sorted(session_notes_dir.glob("*.md"), key=mtime, reverse=True)
        for note_path in notes[:10]:  // Only recent 10
            frontmatter = parse_frontmatter_only(note_path)
            if frontmatter:  // Skip old notes without frontmatter
                session_headers.append(MemoryHeader(
                    filename=note_path.name,
                    title=frontmatter["title"],
                    summary=frontmatter["summary"],
                    type=SESSION_NOTE,
                    mtime=note_path.stat().st_mtime,
                ))

    // Step 2: Build two-section manifest
    manifest = ""

    // Section 1: Memories
    if memory_headers:
        manifest += "## Memories\n\n"
        for h in memory_headers:
            age = memory_age_text(h.mtime)
            manifest += f"[{h.type}] {h.filename} ({age}): {h.summary}\n"
        manifest += "\n"

    // Section 2: Recent Chats
    if session_headers:
        manifest += "## Recent Chats\n\n"
        for h in session_headers:
            age = memory_age_text(h.mtime)
            manifest += f"[session] {h.filename} ({age}): {h.summary}\n"

    // Step 3: LLM semantic selection (single call, temperature=0)
    selected = pantheon.utils.llm.acompletion(
        model = selection_model,  // "low" quality level
        messages = [
            system: LLM_SELECTION_SYSTEM.format(
                max_memories=5, max_chats=3
            ),
            user: f"Query: {query}\n\nManifest:\n{manifest}"
        ],
        response_format = { type: "json_object" }
    )
    data = json.loads(selected)
    memory_filenames = data["selected_memories"]  // ≤5
    chat_filenames = data["selected_chats"]  // ≤3

    // Step 4: Load full content for selected items
    results = []

    // Load memories
    for f in memory_filenames:
        entry = store.read_memory(f)
        if age_days(entry) > 1:
            entry.content += f"\n⚠️ This memory is {age} old. Verify."
        results.append(entry)

    // Load session notes
    for f in chat_filenames:
        path = runtime_dir / "session-notes" / f
        entry = read_session_note(path)  // Parse frontmatter, return MemoryEntry
        if age_days(entry) > 1:
            entry.content += f"\n⚠️ This session note is {age} old."
        results.append(entry)

    // Step 5: Session deduplication (same as before)
    filenames = memory_filenames + chat_filenames
    filenames -= _shown_memories[session_id]
    _shown_memories[session_id] |= filenames

    context["memory_context"] = format(results)
```

**Key changes**:
- Scan both memory-store and session-notes (recent 10 only)
- Two-section manifest: "## Memories" and "## Recent Chats"
- LLM returns two lists: `selected_memories` and `selected_chats`
- Separate limits: max 15 memories + max 5 chats

### 5.3 Agent Memory Operations

No dedicated tools. Agent uses `file_manager` to read/write `.pantheon/memory-store/*.md` directly, guided by `MEMORY_GUIDANCE` in system prompt.

### 5.4 Run End — Three-Step Post-Processing

**① Extract Memories** (background agent, every turn):
- Pending drain pass: When in-flight, new messages marked as pending and processed after current extraction completes
- Cursor snapshot: Advances to position snapshotted at extraction start, not live message count
- Trailing run: Only advances cursor on success; MAX_RETRIES=3 before skipping segment

**② Session Memory** (single LLM call, threshold-triggered, generates YAML frontmatter):
- Pending drain pass: Same pattern as Extract Memories
- Thresholds: init_tokens=10k, update_tokens=5k, tool_calls=3
- **LLM generates complete YAML frontmatter + content**:
  ```
  SESSION_MEMORY_UPDATE_PROMPT = """
  Return your response in standard Markdown format with YAML frontmatter:

  ---
  title: [Brief title, max 100 chars]
  summary: [One-sentence summary, max 200 chars]
  type: session_note
  session_id: {session_id}
  updated: {current_timestamp}
  ---

  ## Task State
  ...
  """
  ```
- **System parses and validates**:
  - Parse YAML frontmatter from LLM response
  - Validate required fields (title, summary)
  - Override system-managed fields (type, session_id, updated, last_message_index)
- **Fallback on format error**:
  - Extract title from first heading
  - Extract summary from Task State section (first 200 chars)
  - Generate frontmatter with extracted values
- **Write with yaml.dump()**: Standard YAML frontmatter format

**③ Dream** (background agent, 4-layer gates, integrates session notes):
- Time interval (24h), scan throttle (10min), session count (5), PID file lock
- **Phase 2 signal sources** (priority order):
  1. **Session notes** (memory-runtime/session-notes/*.md) — Most recent, well-structured
     - Scan frontmatter `summary` field for quick identification
     - Extract cross-session information: project decisions, workflow patterns, user corrections, findings
  2. **Daily logs** (logs/YYYY/MM/YYYY-MM-DD.md) — Append-only stream
  3. **Existing memories that drifted** — Facts that may no longer be accurate
- **Consolidation**: Extract session notes' insights → write to memory-store → permanent retention

### 5.5 Compression Trigger — Session Memory Compact

```
CompressionPlugin._perform_compression(team, memory):
    // Step 1: Pre-flush (call all plugins' pre_compression hook)
    for plugin in team.plugins:
        if plugin is not self:
            result = await plugin.pre_compression(team, session_id, messages)

    // Step 2: Session Memory Compact (zero LLM calls, try first)
    wait_for_session_memory(timeout=15s)
    session_note = read_session_memory(session_id)

    if session_note is not empty:
        boundary = get_session_memory_boundary()
        new_messages = [
            {
                role: "compression",
                content: f"{{{{ CHECKPOINT {compression_index} }}}}\n...{session_note}",
                _metadata: { method: "session_memory_compact", ... }
            },
            ...messages_after_boundary
        ]
        adjust_for_tool_pairs(new_messages)
        memory._messages = new_messages
        return  // Success! Zero LLM calls

    // Step 3: Fallback to Full Compact (requires LLM call)
    compressor.compress(messages)
```

---

## 6. Learning System Core Flows (Pseudocode)

### 6.1 Session Start — Skill Index Injection

```
on_team_created(team):
    headers = store.scan_headers()
    headers = filter_disabled(headers)
    pantheon_dir = get_settings().pantheon_dir

    for agent in team.agents:
        filtered = filter_by_scope(headers, agent.name)
        if filtered:
            index = "\n".join(f"- {h.name}: {h.description}" for h in filtered)
            guidance = SKILLS_GUIDANCE.replace(".pantheon/", f"{pantheon_dir}/")
            agent.instructions += guidance.format(skill_index=index)
```

**SKILLS_GUIDANCE** (mandatory language, aligned with Hermes):
- "## Skills (mandatory)"
- "you MUST load it with skill_view(name)"
- "Err on the side of loading"
- "defines how it should be done here"
- XML structure: `<available_skills>...</available_skills>`
- "Only proceed without loading if genuinely none are relevant"

### 6.2 Agent Execution — 3 Tools

```
// Discovery:
skill_list()          → [{name, description, tags}]
skill_view(name)      → {content, linked_files, tags}
skill_view(name, path) → {content}  // Supporting file

// Management (unified entry):
skill_manage(action="create", name, content)
skill_manage(action="patch", name, old_str, new_str)
skill_manage(action="update", name, content)
skill_manage(action="delete", name)

// After each skill write (including delete):
runtime.on_skill_tool_used()  → reset extraction counter (mutual exclusion)
injector.invalidate_cache()   → clear index cache
```

### 6.3 Run End — Auto-Extraction

```
on_run_end(team, result):
    if result.get("question") is not None: return  // Skip sub-agents

    session_id = result.get("chat_id") or "default"
    messages = result.get("messages", [])
    
    // Count tool calls in this run
    tool_calls = sum(len(msg.get("tool_calls") or []) for msg in messages if msg.role == "assistant")
    extractor.increment_run(session_id, by=max(tool_calls, 1))  // Minimum 1

    asyncio.create_task(safe_extract()):
        // Gates:
        if counter < nudge_interval (5): return
        if lock.locked():
            _pending[session_id] = True  // Mark pending instead of dropping
            return
        if has_agent_skill_writes(messages): return

        async with lock:
            _pending[session_id] = False
            // Background agent multi-turn reasoning
            agent = await create_background_agent(
                name = "skill-extractor",
                instructions = SKILL_EXTRACTION_PROMPT,
                model = extract_model,
                workspace_path = workspace,
            )
            await agent.run(formatted_messages, use_memory=False)

            injector.invalidate_cache()
            counter = 0
```

**Key improvements**:
- Counter by tool calls: Complex runs (15 tool calls) increment counter by 15, not 1
- Pending flag: When lock held, new calls set `_pending` instead of silently dropping
- Task tracking: `_background_tasks` set prevents GC
- Session isolation: No shared `_current_session_id`, uses `result["chat_id"]` directly

---

## 7. Pantheon Integration

### 7.1 Architecture

```
┌──────────────────────────── PantheonTeam / ChatRoom ────────────────────────────┐
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │ Plugin Registry (pantheon/team/plugin_registry.py)                         │ │
│  │   create_plugins(settings) → sorted by priority                            │ │
│  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐ │ │
│  │   │ MemorySystem │  │ Learning     │  │ Compression                      │ │ │
│  │   │ Plugin (p=50)│  │ Plugin (p=60)│  │ Plugin (p=200)                   │ │ │
│  │   └──────┬───────┘  └──────┬───────┘  └──────────────────────────────────┘ │ │
│  └──────────┼─────────────────┼──────────────────────────────────────────────┘ │
│             │                 │                                                  │
│  ┌──────────▼────────┐  ┌────▼──────────┐                                       │
│  │ MemoryRuntime     │  │ LearningRuntime│  ← singleton (in each plugin module) │
│  │ (singleton)       │  │ (singleton)    │                                       │
│  │                   │  │                │                                       │
│  │ MemoryStore       │  │ SkillStore     │                                       │
│  │ MemoryRetriever   │  │ SkillInjector  │                                       │
│  │ MemoryFlusher     │  │ SkillExtractor │                                       │
│  │ SessionMemory     │  └────────────────┘                                       │
│  │ MemoryExtractor   │                                                           │
│  │ DreamGate         │  ┌────────────────┐                                       │
│  │ DreamConsolidator │  │ SkillToolSet   │  ← injected to all agents            │
│  └───────────────────┘  │ (3 tools)      │                                       │
│                         └────────────────┘                                       │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Plugin Registry — Centralized Registration

```python
@dataclass
class PluginDef:
    name: str
    config_key: str      # settings.json section name
    enabled_key: str     # toggle field within config section
    factory: Callable    # (config, settings) -> TeamPlugin | None
    priority: int = 100  # lower = earlier execution

# Each plugin module self-registers on import:
# memory_system/plugin.py → register_plugin(PluginDef(name="memory_system", priority=50, ...))
# learning_system/plugin.py → register_plugin(PluginDef(name="learning_system", priority=60, ...))

def create_plugins(settings) -> list[TeamPlugin]:
    """One-stop creation of all enabled plugins — shared by ChatRoom and Factory"""
    _ensure_plugins_registered()  # Trigger module imports → self-registration
    plugins = []
    for pdef in sorted_registry:
        config = _get_config(settings, pdef.config_key)
        if config.get(pdef.enabled_key):
            plugin = pdef.factory(config, settings)
            if plugin:
                plugins.append(plugin)
    return plugins
```

### 7.3 Singleton Runtime (Inside Each Plugin Module)

```python
# pantheon/internal/memory_system/plugin.py
_memory_runtime = None

def _create_memory_plugin(config, settings) -> MemorySystemPlugin:
    global _memory_runtime
    if _memory_runtime is None:
        from .runtime import MemoryRuntime
        _memory_runtime = MemoryRuntime(get_memory_system_config(settings))
        _memory_runtime.initialize(resolve_pantheon_dir(settings), resolve_runtime_dir(settings))
    return MemorySystemPlugin(_memory_runtime)

register_plugin(PluginDef(name="memory_system", config_key="memory_system",
                          enabled_key="enabled", factory=_create_memory_plugin, priority=50))
```

### 7.4 TeamPlugin Hook System (8 Hooks)

```python
class TeamPlugin(ABC):
    @abstractmethod
    async def on_team_created(self, team) -> None: ...       # Required
    async def get_toolsets(self, team) -> list[tuple]: ...   # Optional, returns [(toolset, agent_names)]
    async def on_run_start(self, team, user_input, ctx): ... # Optional
    async def on_run_end(self, team, result): ...            # Optional
    async def pre_compression(self, team, sid, msgs): ...    # Optional, returns str|None
    async def post_compression(self, team, result): ...      # Optional
    async def on_tool_call(self, team, name, args, res): ... # Optional
    async def on_shutdown(self): ...                         # Optional
```

`get_toolsets()` is called before `on_team_created`, returns `(toolset_instance, agent_names)` list; `agent_names=None` means inject to all agents.

### 7.5 Configuration (settings.json)

```jsonc
{
    "memory_system": {
        "enabled": true,
        "selection_model": "low",
        "flush_enabled": true,
        "dream_enabled": true,
        "dream_min_hours": 24,
        "dream_min_sessions": 5,
        // Session note thresholds
        "session_note_init_tokens": 10000,
        "session_note_update_tokens": 5000,
        "session_note_tool_calls": 3,
        // Retrieval settings (NEW)
        "selection_max_memories": 15,      // Max memories to select
        "selection_max_chats": 5,         // Max recent chats to select
        "session_notes_retrieval_limit": 15  // Max session notes to scan
    },
    "learning_system": {
        "enabled": true,
        "model": "low",
        "extract_enabled": false,  // Default: agent-driven creation via skill_manage
        "extract_nudge_interval": 5,
        "disabled_skills": []
    }
}
```

---

## 8. Lifecycle Timeline

```
Session Start
    │
    ├─ MemoryPlugin.on_team_created()
    │    └─ Inject MEMORY_GUIDANCE (always) + MEMORY.md index (if exists)
    │
    ├─ LearningPlugin.on_team_created()
    │    └─ Scan skills/ → build index → inject agent.instructions
    │
    ▼
Run 1 Start
    │
    ├─ MemoryPlugin.on_run_start()
    │    └─ LLM selects ≤5 memories + ≤3 recent chats → context["memory_context"]
    │       (Scans memory-store + recent 15 session notes)
    │
    ├─ Agent execution (memory via file_manager, skills via skill_* tools)
    │
    ├─ MemoryPlugin.on_run_end() (non-blocking, returns immediately)
    │    ├─ asyncio.create_task: Extract Memories (background agent + file_manager)
    │    ├─ asyncio.create_task: Session Note Update (LLM generates YAML frontmatter)
    │    └─ asyncio.create_task: Dream check (4-layer gates)
    │
    └─ LearningPlugin.on_run_end() (non-blocking, returns immediately)
         └─ increment counter by tool_calls → counter=3, < 5, skip
    │
Run 2-4 ...
    │
Run 5 End
    │
    └─ LearningPlugin.on_run_end()
         └─ counter=15 ≥ 5 → asyncio.create_task:
              Background agent + file_manager multi-turn
    │
    ▼
Context Compression (when token limit approached)
    │
    ├─ pre_compression hook → MemoryPlugin saves to daily log
    ├─ Session Memory Compact (zero LLM calls)
    │    └─ Replace old messages with session note → 60-80% compression
    └─ Fallback → Full Compact (LLM summary)
    │
    ▼
Run N (24h later, 5+ sessions)
    │
    └─ Dream triggers
         └─ Background agent + file_manager multi-turn consolidation
```

---

## 9. Code Scale

```
Memory System:   15 files, ~2,300 lines core
Learning System: 10 files, ~1,370 lines core
Plugin Registry:  1 file, ~85 lines
Tests:           246 tests (214 unit + 32 integration)
Session Notes Retrieval: +515 lines, -119 lines (8 files modified)
ACE system removed: -8,429 lines (skillbook, pipeline, reflector, skill_manager, etc.)
─────────────────────────────────────────────────────────
Net addition:    ~25 core files + 246 tests
                 0 new external dependencies (only pyyaml; LLM calls via pantheon.utils.llm adapter)
```

---

## 10. Key Design Decisions

### 10.1 Concurrency Model

**Non-blocking extraction**: `on_run_end` fires background tasks via `asyncio.create_task` instead of awaiting, returning immediately (<1ms) to the user. Tasks tracked in `_background_tasks` to prevent GC.

**Pending drain pass**: When extraction is in-flight and new messages arrive, they are marked as pending and processed in a drain pass after current extraction completes. Prevents silent message loss in rapid-fire scenarios.

**Cursor snapshot**: Cursor advances to the position snapshotted at extraction start, not the live message count, preventing race conditions when messages grow during extraction.

### 10.2 Counter Accuracy

**Tool-call based**: `increment_run(by=N)` accumulates by the number of tool calls in each run (minimum 1), not just 1 per run. Complex runs with many tool calls reach the extraction threshold faster, aligning with Hermes behavior.

**Pending flag**: When extraction lock is held, new calls set `_pending` instead of silently dropping. Counter continues to increment regardless of lock state.

### 10.3 Multi-Chat Isolation

**Session isolation**: Removed shared `_current_session_id` attribute that caused multi-chat conflicts. `session_id` now comes directly from `result["chat_id"]`.

**Per-session state**: All in-memory state (cursor, counter, pending flags) keyed by `session_id`, ensuring chat-A and chat-B are fully independent.

### 10.4 Skills Guidance Alignment

**Mandatory language**: Updated `SKILLS_GUIDANCE` prompt to use mandatory language ("you MUST load", "err on the side of loading") and XML structure, aligning with Hermes to ensure agents actually use skills.

**On-demand loading**: Skills are NOT injected as full content into system prompt. Instead, a compact index is injected, and agents load full content via `skill_view()` when needed. This prevents token bloat as skill count grows.

### 10.5 Session Notes Retrieval Design

**YAML frontmatter for efficiency**: Session notes use standard Markdown frontmatter (same as memory-store) for fast indexing. Only frontmatter is read during scanning (first 30 lines), not full content.

**LLM-generated metadata**: `title` and `summary` are generated by LLM (high quality, semantic understanding) rather than mechanical extraction. Fallback extraction ensures reliability if LLM format fails.

**Two-section manifest**: Separating "Memories" and "Recent Chats" in the manifest helps LLM distinguish persistent knowledge from temporary context, improving selection accuracy.

**Separate limits**: Max 15 memories + max 5 chats (configurable). Recent chats have lower limit because they're more temporary and specific to recent work.

**Recent-only scanning**: Only scan recent 15 session notes (configurable) to avoid performance degradation as session count grows. Older sessions' insights are consolidated into memory-store by Dream.

**Dream integration**: Session notes are prioritized as Phase 2 signal source (before daily logs) because they're more structured and recent. Dream extracts cross-session insights and writes to memory-store for permanent retention.

---

## 11. Testing Coverage

**246 tests passing** (214 unit + 32 integration):

- Memory System: 139 tests
  - Pending drain passes (extract_memories, session_note)
  - Cursor snapshot correctness
  - Background task lifecycle
  - Multi-chat isolation
  - Session notes YAML frontmatter generation and parsing
  - Fallback extraction (title/summary)
  - Two-section manifest building
  - LLM selection with two lists

- Learning System: 92 tests
  - Counter increment by tool calls
  - Pending flag behavior
  - Background task tracking
  - Session isolation

- Integration: 15 tests
  - End-to-end memory/learning workflows
  - Plugin lifecycle
  - Compression integration
  - Session notes retrieval integration

All concurrency behaviors covered, no silent failures.

---

## 12. Complete Data Flow Example: Cross-Session Continuity

### Scenario: User continues previous work in a new conversation

```
Day 1, 10:00 - Chat A (session_id="chat-1")
─────────────────────────────────────────────────────────────────

User: "Analyze this scRNA-seq data"

Run 1-5: Agent analyzes data, discovers high mitochondrial ratio (>25%)

↓ on_run_end: Session Note Update (threshold reached)

LLM generates:
---
title: Task: Analyze scRNA-seq QC pipeline
summary: Investigating high mitochondrial ratio (>25%), adjusting QC thresholds
type: session_note
session_id: chat-1
updated: 2026-04-15T10:30:00Z
last_message_index: 42
---

## Task State
Analyzing single-cell RNA-seq data. Discovered high mito ratio (>25%) in 30% of cells.

## Learnings
Brain tissue naturally has higher mitochondrial content. Need tissue-specific thresholds.

... (other sections)

↓ System writes to: .pantheon/memory-runtime/session-notes/chat-1.md


Day 1, 14:00 - Chat B (session_id="chat-2", NEW CONVERSATION)
─────────────────────────────────────────────────────────────────

User: "Continue the scRNA-seq analysis"

↓ on_run_start: Retrieval

Step 1: Scan headers
  memory_headers = [
    MemoryHeader(filename="scrna_pipeline.md", type=PROJECT, summary="scRNA-seq pipeline setup"),
    MemoryHeader(filename="qc_workflow.md", type=WORKFLOW, summary="Standard QC procedure"),
    ...
  ]
  session_headers = [
    MemoryHeader(filename="chat-1.md", type=SESSION_NOTE, 
                 summary="Investigating high mitochondrial ratio (>25%)"),
    ...
  ]

Step 2: Build manifest
  ## Memories
  [project] scrna_pipeline.md (2 days ago): scRNA-seq pipeline setup
  [workflow] qc_workflow.md (1 week ago): Standard QC procedure

  ## Recent Chats
  [session] chat-1.md (today): Investigating high mitochondrial ratio (>25%)

Step 3: LLM Selection
  Query: "Continue the scRNA-seq analysis"
  
  LLM returns:
  {
    "selected_memories": ["scrna_pipeline.md", "qc_workflow.md"],
    "selected_chats": ["chat-1.md"]
  }

Step 4: Load full content
  - Read scrna_pipeline.md (memory-store)
  - Read qc_workflow.md (memory-store)
  - Read chat-1.md (session-notes, parse frontmatter, return content only)

Step 5: Inject to agent
  memory_context = """
  ### scrna_pipeline.md
  [full memory content]

  ### qc_workflow.md
  [full memory content]

  ### Recent Chat: chat-1.md
  ## Task State
  Analyzing single-cell RNA-seq data. Discovered high mito ratio (>25%)...

  ## Learnings
  Brain tissue naturally has higher mitochondrial content...

  [full session note with 9 sections]
  """

↓ Agent receives context

Agent knows:
  ✅ Project background (scrna_pipeline.md)
  ✅ Standard workflow (qc_workflow.md)
  ✅ Previous conversation's detailed progress (chat-1.md)

Agent: "I see you were analyzing scRNA-seq data and found high mitochondrial 
ratio (>25%). Based on the previous analysis, brain tissue naturally has 
higher mito content. Let me continue by adjusting the threshold to 28% as 
planned..."

✅ SEAMLESS CONTINUATION


Day 2, 02:00 - Dream Consolidation
─────────────────────────────────────────────────────────────────

Dream triggers (24h + 5 sessions)

Phase 1: Orient
  - Read MEMORY.md
  - List memory-store/

Phase 2: Gather signal (priority order)
  1. Session notes (memory-runtime/session-notes/*.md)
     - Scan frontmatter summaries
     - Identify chat-1.md: "high mitochondrial ratio" → relevant
     - Read full content

  2. Daily logs (logs/2026/04/2026-04-15.md)
     - Less structured, may contain details

  3. Existing memories
     - Check for drift

Phase 3: Consolidate
  Extract from chat-1.md:
    "High mitochondrial ratio >25% is key finding for brain tissue.
     Need tissue-specific QC thresholds (25-30% for brain vs 15-20% standard)."

  ↓ Write to memory-store/project_scrna_findings.md

  ---
  id: project-scrna-findings
  title: scRNA-seq QC findings - mitochondrial threshold
  type: project
  summary: High mito ratio discovered, tissue-specific threshold needed
  updated: 2026-04-16T02:15:00Z
  ---

  **Finding**: Analysis revealed mitochondrial gene ratio >25% in brain tissue.

  **Why**: Brain tissue naturally has higher mitochondrial content than other tissues.

  **How to apply**: Use tissue-specific QC thresholds:
  - Brain: 25-30% mito threshold
  - Other tissues: 15-20% mito threshold (standard)

  **Reference**: Literature review confirmed brain tissue exception

Phase 4: Update index
  Update MEMORY.md:
  - [scRNA-seq QC findings](memory-store/project_scrna_findings.md) — High mito ratio, tissue-specific thresholds

✅ PERMANENT RETENTION (even if chat-1.md is overwritten)


Day 3 - Chat C (session_id="chat-3", NEW CONVERSATION)
─────────────────────────────────────────────────────────────────

User: "What was the conclusion from the scRNA analysis?"

↓ Retrieval

LLM selects:
  {
    "selected_memories": ["project_scrna_findings.md"],
    "selected_chats": []
  }

Agent: "The key finding was that brain tissue has naturally higher 
mitochondrial content (>25%), requiring tissue-specific QC thresholds 
(25-30% for brain vs 15-20% standard). This was confirmed by literature 
review."

✅ CONCLUSION PRESERVED (even though chat-1.md may be gone)
```

### Key Observations

1. **Immediate continuity**: Chat B can continue Chat A's work immediately (same day)
2. **Long-term retention**: Dream consolidates insights into memory-store (next day)
3. **Graceful degradation**: Even if session notes are overwritten, important findings are preserved
4. **Efficient retrieval**: Only recent 15 session notes scanned, not all history
5. **Clear separation**: Memories (persistent) vs Recent Chats (temporary) in manifest
6. **Quality metadata**: LLM-generated title/summary enable accurate selection


---

## 13. LLM API Call Analysis

### Per-Run LLM Calls

| Phase | Component | Frequency | Model | Purpose | Blocking? |
|-------|-----------|-----------|-------|---------|-----------|
| **Run Start** | Memory Retrieval | Every run | `low` | Select relevant memories/chats | ✅ Yes (in critical path) |
| **Run End** | Extract Memories | Every run | `low` | Extract durable memories | ❌ No (background) |
| **Run End** | Session Note Update | Threshold-triggered | `low` | Update session summary | ❌ No (background) |
| **Run End** | Dream Check | Gate-controlled | `low` | 4-phase consolidation | ❌ No (background) |
| **Run End** | Skill Extraction | Counter-triggered | `low` | Extract skills (if enabled) | ❌ No (background) |

### Detailed Breakdown

#### 1. Memory Retrieval (Every Run, Blocking)

**Frequency**: Every run (100%)

**LLM Call**:
```python
# Single call, temperature=0, JSON mode
response = acompletion(
    model="low",  # Fast model (e.g., gemini-2.0-flash-thinking-exp-01-21)
    messages=[
        {"role": "system", "content": LLM_SELECTION_SYSTEM},
        {"role": "user", "content": f"Query: {query}\n\nManifest:\n{manifest}"}
    ],
    response_format={"type": "json_object"}
)
```

**Input size**:
- System prompt: ~500 tokens
- Manifest: ~200-500 tokens (200 memory headers + 15 session note headers)
- Total: ~700-1,000 tokens

**Output size**: ~50-100 tokens (JSON with filenames)

**Latency**: ~200-500ms (fast model, small input)

**Cost**: Negligible (low-tier model, small tokens)

---

#### 2. Extract Memories (Every Run, Background)

**Frequency**: Every run (100%)

**LLM Call**:
```python
# Background agent with file_manager, multi-turn
agent = create_background_agent(
    name="memory-extractor",
    instructions=EXTRACT_MEMORIES_SYSTEM,
    model="low",
    tools=["file_manager"]
)
await agent.run(conversation_context)
```

**Input size**:
- System prompt: ~2,000 tokens
- Conversation context: ~5,000-20,000 tokens (recent messages)
- Existing memories: ~1,000-5,000 tokens (read via file_manager)
- Total: ~8,000-27,000 tokens

**Output size**: ~500-2,000 tokens (new/updated memories)

**Turns**: 2-5 turns (read existing → write new)

**Latency**: ~2-10 seconds (background, non-blocking)

**Cost**: Low (low-tier model, but multi-turn)

---

#### 3. Session Note Update (Threshold-Triggered, Background)

**Frequency**: ~20-30% of runs (threshold: 10K init, 5K growth, or 3 tool calls)

**LLM Call**:
```python
# Single call, generates YAML frontmatter + content
response = acompletion(
    model="low",
    messages=[
        {"role": "system", "content": SESSION_MEMORY_UPDATE_PROMPT},
        {"role": "user", "content": f"Current: {current_note}\n\nNew: {new_messages}"}
    ]
)
```

**Input size**:
- System prompt: ~1,000 tokens
- Current note: ~2,000-5,000 tokens
- New messages: ~3,000-10,000 tokens
- Total: ~6,000-16,000 tokens

**Output size**: ~2,000-5,000 tokens (updated note with 9 sections)

**Latency**: ~1-3 seconds (background, non-blocking)

**Cost**: Low (low-tier model, single call)

---

#### 4. Dream Consolidation (Gate-Controlled, Background)

**Frequency**: ~0.1% of runs (gates: 24h interval + 5 sessions + scan throttle + PID lock)

**LLM Calls**:
```python
# Background agent with file_manager, 4-phase multi-turn
agent = create_background_agent(
    name="dream-consolidator",
    instructions=DREAM_CONSOLIDATION_SYSTEM,
    model="low",
    tools=["file_manager"]
)
await agent.run()
```

**Input size**:
- System prompt: ~3,000 tokens
- MEMORY.md index: ~5,000-10,000 tokens
- Session notes: ~10,000-30,000 tokens (scan recent 15)
- Daily logs: ~5,000-20,000 tokens
- Existing memories: ~10,000-50,000 tokens (read via file_manager)
- Total: ~33,000-113,000 tokens

**Output size**: ~5,000-20,000 tokens (consolidated memories)

**Turns**: 5-15 turns (orient → gather → consolidate → prune)

**Latency**: ~30-120 seconds (background, non-blocking)

**Cost**: Medium (low-tier model, but large input + multi-turn)

---

#### 5. Skill Extraction (Counter-Triggered, Background, Optional)

**Frequency**: ~10-20% of runs (counter: every 5 tool calls, if enabled)

**Default**: ❌ Disabled (`extract_enabled: false`)

**LLM Call** (if enabled):
```python
# Background agent with file_manager, multi-turn
agent = create_background_agent(
    name="skill-extractor",
    instructions=SKILL_EXTRACTION_SYSTEM,
    model="low",
    tools=["file_manager"]
)
await agent.run(conversation_context)
```

**Input size**:
- System prompt: ~2,000 tokens
- Conversation context: ~5,000-20,000 tokens
- Existing skills: ~1,000-5,000 tokens
- Total: ~8,000-27,000 tokens

**Output size**: ~1,000-5,000 tokens (new/updated skills)

**Turns**: 2-5 turns

**Latency**: ~2-10 seconds (background, non-blocking)

**Cost**: Low (low-tier model, but multi-turn)

---

### Summary: LLM Calls Per Run

| Scenario | Blocking Calls | Background Calls | Total Calls | User-Perceived Latency |
|----------|----------------|------------------|-------------|------------------------|
| **Typical run** | 1 (retrieval) | 1-2 (extract + session note) | 2-3 | ~200-500ms |
| **Dream trigger** | 1 (retrieval) | 3 (extract + session note + dream) | 4 | ~200-500ms |
| **Skill extraction** | 1 (retrieval) | 3 (extract + session note + skill) | 4 | ~200-500ms |
| **All triggers** | 1 (retrieval) | 4 (all background) | 5 | ~200-500ms |

**Key Insight**: Only 1 blocking call per run (retrieval), all others are background.

---

### Cost Estimation (Approximate)

Assuming:
- Low-tier model: $0.10 per 1M input tokens, $0.30 per 1M output tokens
- 100 runs per day
- Dream triggers once per day

| Component | Daily Calls | Input Tokens | Output Tokens | Daily Cost |
|-----------|-------------|--------------|---------------|------------|
| Retrieval | 100 | 100K | 5K | $0.01 + $0.002 = **$0.012** |
| Extract Memories | 100 | 1.5M | 100K | $0.15 + $0.03 = **$0.18** |
| Session Note | 25 | 250K | 75K | $0.025 + $0.023 = **$0.048** |
| Dream | 1 | 70K | 10K | $0.007 + $0.003 = **$0.01** |
| **Total** | **226** | **1.92M** | **190K** | **$0.25/day** |

**Monthly cost**: ~$7.50 (for 100 runs/day)

**Note**: Actual cost depends on:
- Model pricing (varies by provider)
- Conversation length (longer = more tokens)
- Memory/skill count (more = larger context)

---

### Optimization Strategies

1. **Retrieval is already optimized**:
   - Fast model (`low` tier)
   - Small input (only frontmatter, not full content)
   - JSON mode (structured output)
   - Temperature=0 (deterministic, no sampling overhead)

2. **Background tasks are non-blocking**:
   - User never waits for extraction/consolidation
   - Tasks run in parallel with user's next action

3. **Thresholds prevent over-extraction**:
   - Session note: only updates when significant growth
   - Dream: 24h + 5 sessions gates
   - Skill extraction: disabled by default

4. **Cursor snapshot prevents re-processing**:
   - Extract Memories only processes new messages
   - No redundant LLM calls on same content

5. **Pending drain prevents message loss**:
   - When extraction is in-flight, new messages are queued
   - Single drain pass after completion (not multiple calls)


---

## 14. Simplified Flow Diagram

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         User Conversation                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          PantheonTeam / ChatRoom                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                         Agent Execution                           │  │
│  │  • Receives user input                                            │  │
│  │  • Executes with tools (file_manager, skill_*, etc.)             │  │
│  │  • Returns response                                               │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                    │                                     │
│         ┌──────────────────────────┼──────────────────────────┐         │
│         ▼                          ▼                          ▼         │
│  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐    │
│  │   Memory    │          │  Learning   │          │ Compression │    │
│  │   Plugin    │          │   Plugin    │          │   Plugin    │    │
│  └─────────────┘          └─────────────┘          └─────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
         │                          │                          │
         ▼                          ▼                          ▼
┌─────────────────┐        ┌─────────────────┐      ┌─────────────────┐
│ Memory Runtime  │        │ Learning Runtime│      │ Session Memory  │
│                 │        │                 │      │ Compact         │
│ • Retrieval     │        │ • Skill Store   │      │                 │
│ • Extraction    │        │ • Injector      │      │ (Zero LLM call) │
│ • Session Note  │        │ • Extractor     │      │                 │
│ • Dream         │        │                 │      │                 │
└─────────────────┘        └─────────────────┘      └─────────────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐        ┌─────────────────┐
│ .pantheon/      │        │ .pantheon/      │
│ memory-store/   │        │ skills/         │
│ MEMORY.md       │        │                 │
│ memory-runtime/ │        │ skills-runtime/ │
└─────────────────┘        └─────────────────┘
```

---

### Per-Run Flow (Simplified)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Session Start                                 │
│  • Inject MEMORY.md index into agent.instructions                       │
│  • Inject skill index into agent.instructions                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                             Run Start                                    │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Memory Retrieval (BLOCKING, ~200-500ms)                        │    │
│  │  1. Scan memory-store (200 headers) + session-notes (15)      │    │
│  │  2. Build two-section manifest                                 │    │
│  │  3. LLM selects ≤15 memories + ≤5 chats                       │    │
│  │  4. Load full content                                          │    │
│  │  5. Inject into context["memory_context"]                      │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Agent Execution                                  │
│  • Agent receives memory_context + skill index                          │
│  • Executes with tools (file_manager, skill_*, etc.)                    │
│  • Returns response to user                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Run End (NON-BLOCKING)                              │
│  All tasks fire via asyncio.create_task() and return immediately        │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Task 1: Extract Memories (Background, ~2-10s)                  │    │
│  │  • Background agent reads conversation                          │    │
│  │  • Extracts durable memories via file_manager                   │    │
│  │  • Writes to memory-store/*.md                                  │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Task 2: Session Note Update (Background, ~1-3s)                │    │
│  │  • Check threshold (10K init / 5K growth / 3 tool calls)       │    │
│  │  • LLM generates YAML frontmatter + 9 sections                 │    │
│  │  • Writes to memory-runtime/session-notes/<id>.md              │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Task 3: Dream Check (Background, ~30-120s if triggered)        │    │
│  │  • Check gates (24h + 5 sessions + scan throttle + PID lock)   │    │
│  │  • If triggered: 4-phase consolidation                         │    │
│  │    Phase 1: Orient (read MEMORY.md, list memory-store)         │    │
│  │    Phase 2: Gather (read session notes, daily logs)            │    │
│  │    Phase 3: Consolidate (extract → write to memory-store)      │    │
│  │    Phase 4: Prune (update MEMORY.md index)                     │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Task 4: Skill Extraction (Background, ~2-10s, if enabled)      │    │
│  │  • Increment counter by tool_calls                             │    │
│  │  • If counter >= 5: background agent extracts skills           │    │
│  │  • Writes to skills/<name>/SKILL.md                            │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                          User receives response
                    (Background tasks continue running)
```

---

### Compression Flow (Simplified)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Context Limit Approached                              │
│  CompressionPlugin detects token limit                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Pre-Compression Hook                                  │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ MemoryPlugin.pre_compression()                                 │    │
│  │  • Flush important info to daily log                           │    │
│  │  • Flush to session log                                        │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              Session Memory Compact (Zero LLM Call)                      │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ 1. Wait for session note update to complete (max 15s)          │    │
│  │ 2. Read session note from memory-runtime/session-notes/        │    │
│  │ 3. If not empty:                                               │    │
│  │    • Get boundary (last_message_index from frontmatter)        │    │
│  │    • Replace messages[:boundary] with session note             │    │
│  │    • Keep messages[boundary:] (after boundary)                 │    │
│  │    • Success! Zero LLM calls                                   │    │
│  │ 4. If empty: fallback to Full Compact (requires LLM call)     │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                          Compressed conversation
                    (Agent continues with more context space)
```

---

### Key Observations

1. **Only 1 blocking LLM call per run**: Memory Retrieval (~200-500ms)
2. **All extraction is background**: User never waits for memory/skill extraction
3. **Session Memory Compact is zero-cost**: No LLM call if session note exists
4. **Dream is rare and background**: ~0.1% of runs, never blocks user
5. **Skill extraction is optional**: Disabled by default, agent-driven preferred

