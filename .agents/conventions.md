# Conventions

## Environment Setup

```bash
# Create virtual environment and install dependencies
uv sync

# Activate the environment
source .venv/bin/activate

# Install with optional features
uv sync --extra knowledge   # RAG/vector search
uv sync --extra claw         # Multi-channel gateway (Slack, Telegram, etc.)
uv sync --extra r            # R language support
uv sync --extra dev          # Development tools (pytest, etc.)
```

## Common Commands

### CLI

```bash
pantheon cli                  # Start interactive REPL
pantheon ui                   # Start Chatroom UI (auto-starts NATS)
pantheon setup                # Configure LLM provider API keys
pantheon store                # Browse/install from Pantheon Store
pantheon update-templates     # Refresh .pantheon/ from factory defaults
```

### Testing

```bash
pytest                        # Run all tests (excluding marked ones)
pytest -m api                 # Tests requiring API access
pytest -m integration         # Tests requiring heavy deps (scanpy)
pytest -m live_llm            # Tests requiring live LLM calls (needs OPENAI_API_KEY)
pytest -m "not api and not integration and not live_llm"  # Fast local tests only
```

Test markers are defined in `tests/conftest.py`. The conftest also sets up isolated
cache directories (numba, matplotlib, XDG) to avoid side effects.

## Coding Conventions

### Async-First

The codebase is primarily async. Agent message handling, tool execution, and NATS
communication are all async. Use `async def` for new tool methods and agent interactions.

### ToolSet Pattern

All toolsets inherit from `ToolSet` (in `pantheon/toolset.py`). Tools are decorated
with `@tool`:

```python
from pantheon.toolset import ToolSet, tool

class MyToolSet(ToolSet):
    @tool
    async def my_tool(self, param: str) -> str:
        """Tool description shown to the LLM agent."""
        return result
```

Key points:
- `@tool` auto-injects `context_variables` and `session_id` via contextvars
- Use `@tool(exclude=True)` to hide a tool from the LLM
- Access execution context with `get_current_context_variables()`
- Use `context.call_agent()` for intermediate LLM sampling within a tool
- Register new toolsets in `pantheon/toolsets/__init__.py` via `_TOOLSET_MAPPING`

### Lazy Imports

The toolsets module uses lazy `__getattr__` imports to avoid loading heavy dependencies
at startup. Follow this pattern when adding new toolsets:

```python
# In pantheon/toolsets/__init__.py
_TOOLSET_MAPPING = {
    "MyNewToolSet": ".my_module",
    ...
}
```

### Configuration

- Use `pantheon/settings.py` Settings class for accessing config values
- Config files are JSONC (JSON with comments support)
- 3-layer merge: user global > project > factory defaults
- Environment variables and `.env` files can override settings

### Pydantic Models

Use Pydantic for data validation in API boundaries, config models, and tool parameters.

## Do / Don't

### Do

- Write async code for agent and tool interactions
- Inherit from `ToolSet` for new tool groups
- Use litellm for all LLM calls (never call provider APIs directly)
- Add test markers (`@pytest.mark.api`, etc.) for tests with external dependencies
- Use `loguru` logger (`from pantheon.utils.log import logger`)
- Support the 3-layer config hierarchy when adding new settings
- Use lazy imports for heavy dependencies (scanpy, torch, etc.)

### Don't

- Don't call LLM providers directly — always go through litellm
- Don't use synchronous I/O in agent or tool execution paths
- Don't hardcode API keys — use settings or environment variables
- Don't import heavy packages at module top level (use lazy imports)
- Don't modify `pantheon/factory/templates/` for project-specific config
  (use `.pantheon/` instead)
- Don't skip `@tool` decorator — it handles context injection and serialization
