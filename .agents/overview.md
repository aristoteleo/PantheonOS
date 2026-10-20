# Project Overview

## What is PantheonOS?

PantheonOS is an **evolvable and privacy-preserving multi-agent framework** for building
distributed, scalable intelligent systems. It specializes in automating end-to-end
workflows with a focus on domain specificity — particularly single-cell biology analyses.

Key capabilities:
- **Evolvable agents** — Genetic-algorithm-driven code evolution (Pantheon-Evolve)
- **Multi-agent orchestration** — Sequential, Swarm, MoA, AgentAsTool team patterns
- **Distributed architecture** — NATS-based messaging for fault-tolerant deployments
- **Interactive interfaces** — CLI (`pantheon cli`) and Chatroom UI (`pantheon ui`)
- **Pantheon Store** — Community marketplace with 1,000+ curated agents, teams, and skills
- **MCP integration** — Native Model Context Protocol server support

## Tech Stack

- **Language**: Python 3.10+
- **LLM abstraction**: litellm (multi-provider: OpenAI, Anthropic, etc.)
- **Messaging**: NATS (nats-py) for distributed multi-agent communication
- **MCP**: fastmcp for Model Context Protocol servers
- **CLI/UI**: rich, prompt-toolkit, fire
- **Execution**: executor-engine for distributed job execution
- **Code analysis**: tree-sitter with Python/JS parsers
- **Data/Scientific**: pandas, scanpy, numpy, jupyter-client
- **RAG**: llama-index, qdrant-client, lancedb
- **Package manager**: uv (recommended)

## Entry Points

The main CLI is exposed as `pantheon` (defined in `apps/agent/pantheon/__main__.py`):

| Command | Description |
|---------|-------------|
| `pantheon cli` | Start interactive REPL |
| `pantheon ui` | Start Chatroom UI with NATS |
| `pantheon setup` | Launch setup wizard for LLM API keys |
| `pantheon update-templates` | Update .pantheon/ templates from factory defaults |
| `pantheon store` | Access the Pantheon Store (browse, install, publish) |

## Configuration Hierarchy

Settings are loaded with **3-layer priority** (highest first):

1. `~/.pantheon/settings.json` — User global config
2. `.pantheon/settings.json` — Project-level config
3. `apps/agent/pantheon/factory/templates/settings.json` — Package defaults

All config files support JSONC (JSON with comments). Environment variables and `.env`
files can override any setting.

## Execution Flow

```
User Input (CLI / UI / API)
    └─> REPL (repl/) or Chatroom (chatroom/)
        └─> Team routing (team/)
            └─> Agent (agent.py) → LLM call via litellm
                └─> Tool execution
                    ├─> Local ToolSet (toolsets/)
                    ├─> Remote ToolSet (endpoint/toolsets.py)
                    └─> MCP Server (endpoint/mcp.py)
                        └─> Response (streamed or batched)
```

---

# Module Reference

## agent.py — Core Agent

The central Agent class (~2,500 lines). Handles LLM interaction, tool dispatch,
memory, and streaming.

- **Key class**: `Agent` — main public API for creating and running agents
- **Key helpers**: `ExecutionContext`, `AgentRunContext`, `ToolInfo`, `ToolProvider`
- **LLM calls**: Via litellm (supports OpenAI, Anthropic, local models, etc.)
- **Tool dispatch**: Discovers tools from attached ToolSets and MCP servers
- **Note**: This is the largest single file. Most features route through here.

## team/ — Multi-Agent Team Patterns

Orchestrates multiple agents working together.

| File | Pattern | Description |
|------|---------|-------------|
| `base.py` | Base | Abstract team base class |
| `pantheon.py` | PantheonTeam | Main team used in chatroom/REPL with smart routing |
| `sequential.py` | Sequential | Agents execute in a defined order |
| `swarm.py` | Swarm | Decentralized agent collaboration |
| `moa.py` | MoA | Mixture-of-Agents (ensemble voting) |
| `aat.py` | AgentAsTool | Wrap agent teams as callable tools |
| `plugin.py` | Plugin | Plugin support for team extensions |

When to use which: Sequential for pipelines, Swarm for dynamic routing,
MoA for consensus, AgentAsTool for composability.

## toolsets/ — Specialized Tool Groups

17+ toolsets, each a subpackage under `pantheon/toolsets/`. All inherit from `ToolSet`.

| Toolset | Module | Purpose |
|---------|--------|---------|
| PythonInterpreterToolSet | `python/` | Safe Python execution sandbox |
| ShellToolSet | `shell/` | OS command execution |
| FileManagerToolSet | `file/` | File read/write/search operations |
| NotebookToolSet | `notebook/` | Jupyter notebook execution |
| SCFMToolSet | `scfm/` | Single-cell genomics (scanpy, scverse) |
| VectorRAGToolSet | `rag/` | Retrieval-augmented generation |
| KnowledgeToolSet | `knowledge/` | Knowledge base management |
| CodeToolSet | `code/` | Code parsing and AST analysis |
| EvolutionToolSet | `evolution/` | Code evolution support |
| DatabaseAPIQueryToolSet | `database_api/` | Database interaction |
| TaskToolSet | `task/` | Task scheduling |
| PackageToolSet | `package.py` | Package management |
| SkillbookToolSet | `skillbook.py` | Skill management |
| RInterpreterToolSet | `r/` | R language execution |
| JuliaInterpreterToolSet | `julia/` | Julia language execution |
| WebToolSet | `web.py` | Web search/fetch |
| ScraperToolSet | `scraper.py` | Web scraping |

- **Registry**: `toolsets/__init__.py` uses `_TOOLSET_MAPPING` with lazy `__getattr__`
- **Adding a toolset**: Create subpackage, inherit `ToolSet`, add to `_TOOLSET_MAPPING`

## evolution/ — Agentic Code Evolution

Genetic-algorithm-driven code improvement engine.

- `team.py` — Evolution team logic (fitness evaluation + mutation + selection)
- `database.py` — Evolution history and program storage
- `evaluator.py` — Fitness function evaluation
- `prompt_builder.py` — Prompt generation for code mutations
- `program.py` — Program representation (code + metadata)
- `config.py` — Evolution hyperparameters
- `visualizer.py` — Interactive HTML result reports
- **Note**: Relatively self-contained. `evolution/team.py` is the main orchestrator.

## chatroom/ — Multi-Agent Chatroom

Real-time multi-agent collaboration with WebSocket and NATS.

- `room.py` — Main chatroom implementation (~93KB, the largest file)
- `start.py` — Service startup, NATS server management
- `nats_manager.py` — NATS pub/sub event management
- `special_agents.py` — Special agent types for chatroom
- `stream.py` / `thread.py` — Stream and thread handling
- **Note**: Depends on NATS. The `pantheon ui` command auto-starts a NATS server.

## repl/ — Interactive CLI

Full-featured terminal REPL with rich formatting.

- `core.py` — Core REPL loop and agent interaction
- `prompt_app.py` — Prompt-toolkit input handling
- `ui.py` — Terminal UI layout and rendering
- `renderers.py` / `task_renderers.py` — Output formatting
- `setup_wizard.py` — First-run setup for API keys
- **Note**: Entry point is `pantheon cli`. Uses rich + prompt-toolkit.

## endpoint/ — Service Endpoints & MCP

HTTP/WebSocket service layer and MCP protocol implementation.

- `core.py` — HTTP/WS endpoint core
- `mcp.py` — MCP server protocol (~39KB)
- `toolsets.py` — Toolset proxy service (~29KB)
- `gateway.py` — API gateway
- `hub.py` — Hub service
- `toolset_proxy.py` — Proxies remote toolsets as local
- **Note**: `mcp.py` is the MCP server implementation. Uses `fastmcp` library.

## claw/ — Multi-Channel Gateway

Routes agent interactions across messaging platforms.

- `bridge.py` — Main channel bridge
- `manager.py` — Channel lifecycle management
- `runtime.py` — Runtime management
- `config.py` / `registry.py` — Channel configuration and discovery
- `channels/` — Per-platform adapters: Slack, Telegram, Discord, Feishu, WebSocket
- **Note**: Optional dependency. Install with `uv sync --extra claw`.

## settings.py — Configuration System

Three-layer JSONC config with deep merge.

- `Settings` class — Main config manager
- Config sources: `~/.pantheon/` > `.pantheon/` > `factory/templates/`
- Supports env var overrides and `.env` files

## utils/ — Utilities

Shared helpers used across all modules.

| File | Purpose |
|------|---------|
| `llm.py` | Core LLM interaction, message handling, streaming |
| `model_selector.py` | Intelligent model selection and fallback chains |
| `llm_providers.py` | Provider configuration and API key management |
| `vision.py` | Image/vision utilities (base64, resize, etc.) |
| `truncate.py` | Message truncation for context window limits |
| `display.py` | Terminal display helpers |
| `message_formatter.py` | Message formatting for different contexts |
| `memory_compress.py` | Memory compression strategies |
| `misc.py` | General utilities |
| `log.py` | Loguru logger configuration |

## internal/ — Internal Subsystems

Lower-level systems not meant for direct external use.

- `memory/` — Agent memory management
- `learning/` — Skill injection and learning
- `compression/` — Message compression for long conversations
- `message/` — Message formatting internals
- `package_runtime/` — Runtime context and state management
- `injector.py` — Dependency injection

## Module Relationships

```
                   repl/  ←→  chatroom/
                     ↓           ↓
                   team/ (routing & orchestration)
                     ↓
                  agent.py (core LLM interaction)
                   ↙   ↘
           toolsets/    endpoint/ (MCP, remote toolsets)
              ↓              ↓
          [execution]    [external services]

settings.py  ← used by all modules
utils/       ← used by all modules
internal/    ← used by agent.py, team/, chatroom/
```

---

# Team Templates

Team templates live in `apps/agent/pantheon/factory/templates/teams/` as Markdown files with YAML
frontmatter. Each template defines agents, their toolsets, and orchestration instructions.

## Default Team (`teams/default.md`)

General-purpose team with a **delegation-first architecture**.

**Agents**:

| Agent | Toolsets | Role |
|-------|----------|------|
| **leader** | file_manager, shell, package, task, integrated_notebook, web, evolution, think | Orchestrator — reserves context for decisions, delegates information-gathering |
| **researcher** | _(delegated tasks)_ | Information gathering specialist |
| **scientific_illustrator** | _(delegated tasks)_ | Visualization expert |

**Design principle**: The leader delegates ALL information-gathering to sub-agents and
only executes directly for simple lookups or synthesizing results.

## Single-Cell Team (`teams/single_cell_team.md`)

Specialized 7-agent team for single-cell / spatial omics workflows.

**Agents**:

| Agent | Toolsets | Role |
|-------|----------|------|
| **leader** | file_manager, shell, task | Workflow orchestration, delegation, workdir organization |
| **fm_router** | _(implicit)_ | scFM task routing and model selection |
| **analysis_expert** | file_manager, integrated_notebook | Python/notebook analysis, QC, visualization |
| **biologist** | _(implicit)_ | Hypothesis generation, biological interpretation |
| **reporter** | _(delegated)_ | LaTeX/PDF report generation, figure organization |
| **system_manager** | _(delegated)_ | Package installation, environment setup |
| **browser_use** | _(delegated)_ | Web search, literature retrieval |

Agent template files: `apps/agent/pantheon/factory/templates/agents/single_cell/`

**Mandatory workflow loop**:
```
1. Environment setup       → system_manager
2. Dataset analysis         → analysis_expert
3. Hypothesis generation    → biologist
4. Create todolist.md plan
5. Execution loop (iterate per work intensity):
   a. analysis_expert performs ONE analysis step
   b. biologist IMMEDIATELY interprets results
   c. Update todolist, next step
6. Final reporting          → reporter (PDF, then HTML)
```

Work intensity: Low = 1 loop, Medium = 3 loops, High = 5+ loops.

**Key constraints**:
- Shared data directory for reusable processed datasets across loops
- Absolute paths only (no relative paths)
- Each loop follows strict sequence — never batch multiple analysis calls

## Inter-Agent Communication (PantheonTeam)

`PantheonTeam` (`apps/agent/pantheon/team/pantheon.py`) adds delegation tools to every agent:

- `list_agents()` — Discover available agents and their descriptions
- `call_agent(agent_name, instruction)` — Delegate a task to another agent
  (creates isolated child memory, tracks delegation depth)
- `transfer_to_agent(target_name)` — Hand off control (optional, if `allow_transfer=True`)

Delegation chains are tracked with format `root_id|d{depth}|agent_slug|rand4`
to detect loops. Max depth defaults to 5.

---

# Task Toolset

The Task toolset (`pantheon/toolsets/task/`) is a **local-only** toolset that provides
modal workflow management. Unlike other toolsets that run via the Endpoint, TaskToolSet
is instantiated directly on the agent.

## Core Tools

### `task_boundary(task_name, mode, task_summary, task_status, predicted_task_size)`

Signals the start or transition of a work phase.

- **task_name**: Identifier matching items in a task plan (e.g., todolist.md)
- **mode**: Work phase — `PLANNING` / `EXECUTION` / `VERIFICATION` or
  `RESEARCH` / `ANALYSIS` / `INTERPRETATION`
- **task_summary**: What has been accomplished (past tense)
- **task_status**: What is next (future tense)
- **predicted_task_size**: Estimated tool calls needed
- Supports `%SAME%` substitution to reuse previous values

### `notify_user(paths_to_review, blocked_on_user, message, confidence_score, questions)`

Engages the user at decision points.

- **paths_to_review**: Files for user to review
- **blocked_on_user**: Whether to pause and wait
- **confidence_score**: 0.0–1.0 based on a 6-question rubric
- **questions**: Structured interactive questions (auto-sets `interrupt=True`)

## State Tracking

State is persisted to `{brain_dir}/{client_id}/task_state.json` and includes:

- **Active task**: Current task name, mode, status, summary
- **Artifact tracking**: Files created/modified, categorized by role
  (task output, plan, summary, tracker)
- **Tool counters**: Tools since last boundary, last think call, last update

## Ephemeral Message Injection

Before each LLM call, the Task toolset generates an **ephemeral message** (EU)
injected into the conversation context. This provides:

- Active task reminder (or explanation that no task is set)
- Artifact list with creation/modification status
- Warning if too many tool calls (>5) without a `task_boundary`
- Think tool usage reminder if >5 tools since last think
- Stale artifact warning if a file not accessed in >10 steps
- Plan artifact modification guard during planning phase

## Mode Semantics

| Phase | Modes |
|-------|-------|
| Plan | PLANNING, RESEARCH, DESIGN |
| Execute | EXECUTION, ANALYSIS, IMPLEMENTATION |
| Verify | VERIFICATION, INTERPRETATION, TESTING, REVIEW |
