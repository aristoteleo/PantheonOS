import asyncio
import copy
import inspect
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Union
from uuid import uuid4


from pantheon.funcdesc import parse_func
from pydantic import BaseModel, create_model

if TYPE_CHECKING:
    from .internal.memory import Memory

from .agent_loop import AgentHooks, run_turns
from .toolset import ToolSet
from .utils.log import logger
from .utils.misc import desc_to_openai_dict, run_func
from .utils.tool_pairing import ensure_tool_result_pairing_with_stats
if TYPE_CHECKING:
    from .utils.vision import VisionInput

def _get_default_model() -> list[str]:
    """Get default model fallback chain from ModelSelector.

    Uses environment API keys to detect available providers and
    returns appropriate default models.

    Returns:
        List of models as fallback chain
    """
    try:
        from .utils.model_selector import get_default_model

        return get_default_model()
    except Exception as e:
        from .utils.model_selector import ULTIMATE_FALLBACK

        logger.warning(f"Failed to get default model from selector: {e}")
        return [ULTIMATE_FALLBACK]


def _parse_thinking_suffix(model_str: str) -> tuple[str, str | None]:
    """Strip ``+think[:level]`` suffix from a model string.

    Args:
        model_str: Model name or tag, optionally ending with ``+think`` or
            ``+think:<level>`` where level is any real reasoning-effort tier
            (``none``/``minimal``/``low``/``medium``/``high``/``xhigh``/``max``).

    Returns:
        (clean_model_str, thinking_level) where *thinking_level* is the level
        string (e.g. ``"high"``, ``"xhigh"``, ``"max"``) or ``None``.
    """
    import re

    match = re.search(r"\+think(?::(\w+))?$", model_str)
    if not match:
        return model_str, None
    level = match.group(1) or "high"
    # Keep in sync with the picker's effort ladders (openrouter reasoning.supported_efforts).
    if level not in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
        return model_str, None
    return model_str[: match.start()], level


def _is_model_tag(model_str: str) -> bool:
    """Check if a string is a model tag vs a model name.

    Model tags are quality/capability identifiers like "high", "normal,vision".
    Model names are actual model identifiers like "openai/gpt-5.4", "gpt-5.4-mini".
    A ``+think[:level]`` suffix is allowed on any tag string.

    Args:
        model_str: The string to check

    Returns:
        True if the string is a tag, False if it's a model name
    """
    if not model_str or not model_str.strip():
        return False

    # Strip +think suffix before checking
    clean, _ = _parse_thinking_suffix(model_str)

    # Model names typically contain "/" (provider/model format)
    if "/" in clean:
        return False

    # Check if all parts are known tags
    try:
        from .utils.model_selector import QUALITY_TAGS, CAPABILITY_MAP

        parts = [p.strip().lower() for p in clean.split(",")]
        all_known_tags = QUALITY_TAGS | set(CAPABILITY_MAP.keys())

        return all(part in all_known_tags for part in parts if part)
    except ImportError:
        return False


def _resolve_model_tag(tag: str) -> list[str]:
    """Resolve a tag string to a model list.

    A ``+think[:level]`` suffix is stripped before resolution (it only
    affects ``model_params``, not model selection).

    Args:
        tag: Tag string like "high", "normal,vision", "high+think"

    Returns:
        List of models as fallback chain
    """
    from .utils.model_selector import get_model_selector

    clean, _ = _parse_thinking_suffix(tag)
    return get_model_selector().resolve_model(clean)


def _normalize_model_spec(
    model: str | list[str] | None,
) -> str | list[str] | None:
    """Treat empty string model specs as unspecified."""
    if isinstance(model, str) and not model.strip():
        return None
    return model


def _resolve_model_spec_with_current_provider(
    model: str | list[str] | None,
    current_model: str | None = None,
    *, model_scope=None,
) -> str | list[str] | None:
    """Resolve model tags while preferring the current dialog provider.

    Example: if the active dialog runs on ``codex/gpt-5.4-mini`` and an
    internal helper asks for ``low``, keep the helper on the Codex provider
    instead of letting global auto-selection jump to OpenAI.
    """
    model = _normalize_model_spec(model)
    if not isinstance(model, str) or not _is_model_tag(model):
        return model

    if model_scope is not None:
        clean, effort = _parse_thinking_suffix(model)
        resolved = ([current_model] if (current_model or '').startswith(('fleet-model://', 'fleet-route://'))
                    else model_scope.models(clean))
        if effort is not None:
            resolved = [f'{_parse_thinking_suffix(item)[0]}+think:{effort}' for item in resolved]
        return resolved

    if (current_model or '').startswith(('fleet-model://', 'fleet-route://')):
        # Internal quality tags must retain the explicit Fleet placement too.
        return current_model

    # Strip +think suffix — it doesn't affect model selection
    clean, _ = _parse_thinking_suffix(model)

    if current_model and "/" in current_model:
        provider = current_model.split("/", 1)[0].strip().lower()
        if provider:
            from .utils.model_selector import get_model_selector

            return get_model_selector().resolve_model_for_provider(clean, provider)

    return model


# ===== Execution Context =====


@dataclass
class ExecutionContext:
    """Prepared execution context for agent run."""

    conversation_history: list[dict]
    context_variables: dict
    should_use_memory: bool
    memory_instance: "Memory | None"
    execution_context_id: str | None = None
    input_messages: list[dict] | None = None


@dataclass
class AgentRunContext:
    """Runtime context information for the currently executing Agent.run call."""

    agent: "Agent"
    memory: "Memory | None"
    execution_context_id: str | None = None
    process_step_message: Callable | None = None
    process_chunk: Callable | None = None
    cache_safe_runtime_params: Any | None = None
    cache_safe_prompt_messages: list[dict] | None = None
    cache_safe_tool_definitions: list[dict] | None = None
    context_collapse_manager: Any | None = None
    current_model: str | None = None
    model_retry_after: dict[str, float] = field(default_factory=dict)
    # Maps a call_agent tool_call_id → the child's execution_context_id so the
    # eventual tool_message can be stamped with it. Lets the UI link a
    # call_agent response back to the exact sub-agent invocation, even when
    # the parent launches multiple parallel calls to the same agent name.
    sub_agent_exec_ids: dict[str, str] = field(default_factory=dict)


_RUN_CONTEXT: ContextVar[AgentRunContext | None] = ContextVar(
    "agent_run_context", default=None
)


def get_current_run_context() -> AgentRunContext | None:
    """Get the runtime context for the current Agent.run invocation."""
    return _RUN_CONTEXT.get()


def get_current_run_model() -> str | None:
    """Get the currently executing model for the active agent run, if any."""
    run_context = get_current_run_context()
    if run_context is None:
        return None
    if run_context.current_model:
        return run_context.current_model
    cache_params = getattr(run_context, "cache_safe_runtime_params", None)
    return getattr(cache_params, "model", None)


# ===== Tool Provider Base Class =====


class ToolInfo(BaseModel):
    """Information about a tool"""

    name: str
    description: str
    inputSchema: Optional[dict] = None  # JSON Schema format (OpenAI function schema)


class ToolProvider(ABC):
    """Abstract base class for tool providers (MCP, ToolSet, etc.)

    Tool providers abstract away the source of tools, allowing agents to
    work uniformly with tools from different sources.
    """

    @abstractmethod
    async def list_tools(self) -> list[ToolInfo]:
        """List all available tools from this provider

        Returns:
            List of ToolInfo objects describing available tools
        """
        pass

    @abstractmethod
    async def call_tool(self, name: str, args: dict) -> Any:
        """Call a tool by name with the given arguments

        Args:
            name: Name of the tool to call
            args: Arguments to pass to the tool

        Returns:
            Result from the tool call
        """
        pass

    async def initialize(self):
        """Optional: Initialize the provider (e.g., connect to remote server)

        Called once when the provider is added to an agent.
        """
        pass

    async def shutdown(self):
        """Optional: Clean up provider resources

        Called when the agent is being shut down.
        """
        pass


# A tool call still running after this many seconds moves to the background:
# it keeps running, the model carries on, and its result arrives as a
# notification. The model can wait for it with background_task.
TOOL_FOREGROUND_SECONDS = 60

_CTX_VARS_NAME = "context_variables"
_SKIP_PARAMS = [_CTX_VARS_NAME]
_CLIENT_ID_NAME = "client_id"


class ResponseDetails(BaseModel):
    """
    The ResponseDetails class is used to store the details of the agent response.
    """

    messages: list[dict]
    context_variables: dict
    execution_context_id: str | None = None  # NEW: Context ID for message filtering


class AgentResponse(BaseModel):
    """
    The AgentResponse class is used to store the agent response.
    """

    agent_name: str
    content: Any
    details: ResponseDetails | None
    interrupt: bool = False


AgentInput = Union[
    str,
    BaseModel,
    AgentResponse,
    list[str | BaseModel | dict],
    "VisionInput",
]


class StopRunning(Exception):
    """Raised to interrupt a running agent.

    Optionally carries a partial assistant message (dict) that was being
    streamed when the stop was requested, so callers can persist it.
    """

    def __init__(self, partial_message: dict | None = None):
        super().__init__()
        self.partial_message = partial_message


class Agent:
    """
    The Agent class is the core component of Pantheon,
    providing a flexible interface for creating AI-powered agents with tools,
    memory, and collaboration capabilities.

    Args:
        name: The name of the agent.
        instructions: The instructions for the agent.
            The instructions are the system instructions that the agent will follow.
            All prompt composition (work strategy, delegation, skills, etc.) should be
            done at template parsing time, not at runtime.
        model: The model to use for the agent.
            Can be a single model or list of fallback models.
        model_params: The additional parameters for the model(LLM).
        icon: The icon to use for the agent.
        tools: The tools to use for the agent.
        response_format: The response format to use for the agent.
            It can be a Pydantic model or a function that returns a Pydantic model.
        use_memory: Whether to use memory for the agent. (default: True)
        memory: The memory to use for the agent.
            If not provided, a new memory will be created.
        tool_timeout: Seconds a tool call runs in the foreground before it moves to
            the background, where it keeps running and reports on completion.
            (default: 60)
        relaxed_schema: Use relaxed (non-strict) tool schema mode. (default: False)
        max_tool_content_length: The maximum length of the tool content. (default: 100000)
        description: The description of the agent. (default: None)
    """

    def __init__(
        self,
        name: str,
        instructions: str,
        model: str | list[str] | None = None,
        model_params: dict | None = None,
        icon: str = "🤖",
        tools: list[Callable] | None = None,
        response_format: Any | None = None,
        use_memory: bool = True,
        memory: "Memory | None" = None,
        tool_timeout: int | None = None,
        relaxed_schema: bool = False,
        max_tool_content_length: int | None = None,
        description: str | None = None,
        model_scope=None,
        image_resolver=None,
    ):
        # Parse +think suffix before any processing. Handle BOTH the string form and the
        # LIST (fallback-chain) form — teams build agents with a model list, and a bare
        # list branch left "+think:high" glued onto the id, so provider detection saw an
        # unknown model ("z-ai/glm-5.2+think:high") and the proxy got a bare "glm-5.2".
        self.model_scope = model_scope
        if image_resolver is None and model_scope is not None:
            from .utils.image_resources import BoundImageResolver
            image_resolver = BoundImageResolver(image_root=model_scope.settings.pantheon_dir / 'images')
        if image_resolver is not None and not callable(image_resolver):
            raise ValueError('Supply an explicit image resolver')
        self._image_resolver = image_resolver
        thinking_level: str | None = None
        if isinstance(model, str):
            model, thinking_level = _parse_thinking_suffix(model)
        elif isinstance(model, list):
            cleaned: list = []
            for _m in model:
                if isinstance(_m, str):
                    _clean, _think = _parse_thinking_suffix(_m)
                    if _think and thinking_level is None:
                        thinking_level = _think
                    cleaned.append(_clean)
                else:
                    cleaned.append(_m)
            model = cleaned

        model = _normalize_model_spec(model)
        self.id = uuid4()
        self.name = name
        self.instructions = instructions
        self.description = description
        self._model_was_explicit = model is not None

        # Smart model selection: use ModelSelector when no model specified
        if model is None:
            # Get default model fallback chain from ModelSelector
            self.models = model_scope.models(None) if model_scope is not None else _get_default_model()
        elif isinstance(model, str):
            # Check if it's a tag string (e.g., "high", "normal,vision")
            if _is_model_tag(model):
                # Resolve tag to model fallback chain
                self.models = model_scope.models(model) if model_scope is not None else _resolve_model_tag(model)
            else:
                # Regular model name - wrap in list
                self.models = [model]
        else:
            # Already a list of models (fallback chain)
            self.models = list(model)

        self.model_params = model_params or {}
        if thinking_level:
            self.model_params.setdefault("thinking", thinking_level)
        # Tool storage (simplified - unified handling)
        self._base_functions: dict[
            str, Callable
        ] = {}  # All tools (local + remote wrappers)

        if tools:
            for func in tools:
                self.tool(func)

        self.response_format = response_format
        self.use_memory = use_memory
        if memory is None:
            from .internal.memory import Memory
            memory = Memory(str(uuid4()))
        self.memory = memory

        # Store user-specified overrides (if provided, these take priority)
        self._tool_timeout_override = tool_timeout
        self._max_tool_content_length_override = max_tool_content_length

        self.events_queue: asyncio.Queue = asyncio.Queue()
        self.relaxed_schema = relaxed_schema
        self.icon = icon

        # Provider management (MCP, ToolSet, etc.)
        self.providers: dict[str, ToolProvider] = {}  # name -> ToolProvider instance
        self.not_loaded_toolsets: list[str] = []  # Track which toolsets failed to load
        # The owner's installed Apps, added as providers on first use
        # (pantheon/agent_app_directory.py); None for an Agent without them.
        self.app_directory = None
        self._app_directory_loaded = False

        # Background task support
        from .background import BackgroundTaskManager

        self._bg_manager = BackgroundTaskManager()
        self._tool_output_buffers: dict[str, list[str]] = {}
        self._register_bg_tools()

        # Optional steer queue (message queue feature): when set by the chatroom
        # to a SteerQueue, the run loop drains user messages that arrive mid-run
        # so the user can steer the agent without waiting for it to finish.
        # None for agents not running under a chatroom (REPL/SDK) — no-op.
        self._steer_queue = None

        # Extension points of the loop (pantheon/agent_loop.py); plugins attach
        # to them in get_toolsets(). The agent knows nothing of the plugins.
        self.hooks = AgentHooks()

    # Earlier names of the loop's extension points.
    @property
    def _ephemeral_hooks(self) -> list:
        return self.hooks.context

    @property
    def _tool_tracking_hooks(self) -> list:
        return self.hooks.after_tools

    @property
    def _pre_tool_hooks(self) -> list:
        return self.hooks.before_tool

    @property
    def _post_tool_hooks(self) -> list:
        return self.hooks.after_tool

    def _register_bg_tools(self) -> None:
        """Register background task management tools."""
        bg_manager = self._bg_manager

        async def background_task(
            action: str = "list",
            task_id: str = "",
            timeout: int = 10,
        ) -> dict:
            """Manage and monitor background tasks.

            Args:
                action: Operation to perform:
                    - "list": List all background tasks (default when no task_id)
                    - "status": Get status, progress and result of a specific task (requires task_id)
                    - "wait": Wait for a task to complete, up to timeout seconds (requires task_id).
                              Use this instead of polling with repeated status calls.
                    - "cancel": Cancel a running task (requires task_id)
                    - "remove": Remove a task from the list, cancels if still running (requires task_id)
                task_id: ID of the task (e.g. 'bg_1'). Required for status/wait/cancel/remove.
                timeout: Max seconds to wait for the "wait" action (default 10). If the task
                         hasn't completed by then, returns current status so you can decide
                         whether to wait again or do something else.

            Returns:
                dict with task details or task list

            Examples:
                background_task()  # List all tasks
                background_task(action="status", task_id="bg_1")  # Check once
                background_task(action="wait", task_id="bg_1")  # Wait up to 10s
                background_task(action="wait", task_id="bg_1", timeout=30)  # Wait up to 30s
                background_task(action="cancel", task_id="bg_1")  # Cancel task
                background_task(action="remove", task_id="bg_1")  # Remove task
            """
            if action == "list":
                return {
                    "tasks": [
                        bg_manager.to_summary(t) for t in bg_manager.list_tasks()
                    ]
                }

            if action == "status":
                if not task_id:
                    return {"error": "task_id is required for status action"}
                task = bg_manager.get(task_id)
                if task is None:
                    return {"error": f"Task '{task_id}' not found."}
                return bg_manager.to_summary(task)

            elif action == "wait":
                if not task_id:
                    return {"error": "task_id is required for wait action"}
                task = bg_manager.get(task_id)
                if task is None:
                    return {"error": f"Task '{task_id}' not found."}
                # Already finished
                if task.status != "running":
                    return bg_manager.to_summary(task)
                # Wait with polling
                timeout = max(1, min(timeout, 300))  # clamp 1-300s
                poll_interval = 1.0
                elapsed = 0.0
                while elapsed < timeout:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    if task.status != "running":
                        return bg_manager.to_summary(task)
                # Timed out — return current status
                summary = bg_manager.to_summary(task)
                summary["timed_out"] = True
                summary["waited_seconds"] = round(elapsed, 1)
                return summary

            elif action == "cancel":
                if not task_id:
                    return {"error": "task_id is required for cancel action"}
                if bg_manager.cancel(task_id):
                    return {"task_id": task_id, "status": "cancelling"}
                task = bg_manager.get(task_id)
                if task is None:
                    return {"error": f"Task '{task_id}' not found."}
                return {"error": f"Task '{task_id}' is already {task.status}."}

            elif action == "remove":
                if not task_id:
                    return {"error": "task_id is required for remove action"}
                if bg_manager.remove(task_id):
                    return {"task_id": task_id, "status": "removed"}
                return {"error": f"Task '{task_id}' not found."}

            else:
                return {
                    "error": f"Unknown action '{action}'. Must be one of: list, status, wait, cancel, remove"
                }

        self._base_functions["background_task"] = background_task
        # "all": every tool is declared to the model (legacy). "core": only
        # agent_core_tools.CORE_TOOLS; other tools are reached on demand.
        self.tool_exposure = "all"

    def enable_core_tools(self):
        """Declare only the core tools; every other tool is reached on demand
        through describe_app and call_app_tool (pantheon/agent_core_tools.py)."""
        from .agent_core_tools import AGENT_APP

        async def describe_app(app: str) -> dict:
            index = await self._app_tool_index()
            if app not in index and await self._load_app_directory(force=True):
                index = await self._app_tool_index()
            if app not in index:
                return {"error": f"Unknown App '{app}'.", "apps": sorted(index)}
            tools = []
            if app == AGENT_APP:
                for function in self._convert_functions(relaxed_schema=self.relaxed_schema):
                    if function["function"]["name"] in index[app]:
                        tools.append(function["function"])
            else:
                for tool_info in await self.providers[app].list_tools():
                    if tool_info.name in index[app]:
                        tools.append({**tool_info.inputSchema, "name": tool_info.name})
            return {"app": app, "tools": tools}

        async def call_app_tool(app: str, tool: str, args: dict | None = None) -> dict:
            # Reached only when the call could not be resolved to a real tool.
            index = await self._app_tool_index()
            if app not in index and await self._load_app_directory(force=True):
                # An App installed during the conversation.
                index = await self._app_tool_index()
                if tool in index.get(app, ()):
                    return await self.call_tool(f"{app}__{tool}", args or {})
            if app not in index:
                return {"error": f"Unknown App '{app}'.", "apps": sorted(index)}
            return {"error": f"App '{app}' has no tool '{tool}'.", "tools": index[app]}

        self._base_functions["describe_app"] = describe_app
        self._base_functions["call_app_tool"] = call_app_tool
        self.tool_exposure = "core"

    async def _load_app_directory(self, *, force: bool = False) -> bool:
        """Add the owner's installed Apps as providers (docs/agent-app-directory.md).

        Once per conversation, so call_app_tool's description stays the same;
        again (force) only when the model names an App it does not have.
        Returns whether any App was added.
        """
        if self.app_directory is None or (self._app_directory_loaded and not force):
            return False
        providers = await self.app_directory.providers(force=force)
        if providers is None:
            return False
        self._app_directory_loaded = True
        added = [name for name in providers if name not in self.providers]
        for name in added:
            self.providers[name] = providers[name]
        return bool(added)

    async def _app_tool_index(self) -> dict[str, list[str]]:
        """Tools reachable on demand, by App: every provider tool and own
        function that is not declared directly."""
        from .agent_core_tools import AGENT_APP, CORE_TOOLS

        await self._load_app_directory()
        index: dict[str, list[str]] = {}
        own = [name for name in self._base_functions if name not in CORE_TOOLS]
        if own:
            index[AGENT_APP] = own
        async for provider_name, _, tools in self._list_provider_tools():
            names = [t.name for t in tools if f"{provider_name}__{t.name}" not in CORE_TOOLS]
            if names:
                index[provider_name] = names
        return index

    async def _expand_app_tool_calls(self, message: dict) -> None:
        """Record call_app_tool calls as the tool they name (history keeps the
        expanded form; the model's view folds it back)."""
        if self.tool_exposure != "core" or not message.get("tool_calls"):
            return
        from .agent_core_tools import expand_call

        if not any((c.get("function") or {}).get("name") == "call_app_tool" for c in message["tool_calls"]):
            return
        index = await self._app_tool_index()

        def resolve(app, tool):
            if tool.startswith(app + "__"):
                tool = tool[len(app) + 2:]
            if tool in index.get(app, ()):
                return tool if app == "agent" else f"{app}__{tool}"
            return None

        for call in message["tool_calls"]:
            if isinstance(call.get("function"), dict):
                expand_call(call["function"], resolve)

    def _settings(self):
        if self.model_scope is not None:
            return self.model_scope.settings
        from .settings import get_settings
        return get_settings()

    def _image_store(self):
        from .utils.vision import ImageStore, get_image_store
        if self.model_scope is not None:
            return ImageStore(self.model_scope.settings.pantheon_dir / "images")
        return get_image_store()

    def _get_tool_timeout(self) -> int:
        """Seconds a tool call runs in the foreground: user override, else 60."""
        if self._tool_timeout_override is not None:
            return self._tool_timeout_override
        return TOOL_FOREGROUND_SECONDS

    def _get_max_tool_content_length(self) -> int:
        """Get max tool content length with priority: user override > settings."""
        if self._max_tool_content_length_override is not None:
            return self._max_tool_content_length_override
        try:
            return self._settings().max_tool_content_length
        except Exception:
            return 10000

    @property
    def tool_timeout(self) -> int:
        """Public API property for backward compatibility and dynamic access."""
        return self._get_tool_timeout()

    @property
    def max_tool_content_length(self) -> int:
        """Public API property for backward compatibility and dynamic access."""
        return self._get_max_tool_content_length()

    @staticmethod
    def _sanitize_messages(messages: list[dict]) -> list[dict]:
        """Sanitize messages before sending to the LLM.

        1. Drop messages missing a role.
        2. Canonically repair tool-call / tool-result pairing.
        """
        if not messages:
            return []

        # Pass 1: drop messages without a role
        with_role: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            if role is None or (isinstance(role, str) and not role.strip()):
                logger.warning("Dropping message without role: {}", msg)
                continue
            with_role.append(msg)

        cleaned, stats = ensure_tool_result_pairing_with_stats(with_role)

        if stats.dropped_orphan_tool_messages:
            logger.warning(
                "Dropped {} orphaned tool message(s) without matching tool_calls",
                stats.dropped_orphan_tool_messages,
            )
        if stats.dropped_duplicate_tool_calls:
            logger.warning(
                "Dropped {} duplicate assistant tool_call(s)",
                stats.dropped_duplicate_tool_calls,
            )
        if stats.dropped_duplicate_tool_messages:
            logger.warning(
                "Dropped {} duplicate tool response message(s)",
                stats.dropped_duplicate_tool_messages,
            )
        if stats.inserted_placeholder_tool_messages:
            logger.warning(
                "Inserted {} placeholder tool response message(s) for missing tool outputs",
                stats.inserted_placeholder_tool_messages,
            )
        if stats.dropped_empty_assistant_messages:
            logger.warning(
                "Dropped {} empty assistant message(s) after tool_call cleanup",
                stats.dropped_empty_assistant_messages,
            )

        return cleaned

    @property
    def functions(self) -> dict[str, Callable]:
        """Get current tools (returns a copy for safety)."""
        return self._base_functions

    def tool(self, func: Callable, key: str | None = None):
        """
        Add a tool to the agent.

        Args:
            func: The tool function to add to the agent.
            key: The key to use for the tool. If not provided, the name of the function will be used.

        Returns:
            The agent instance.
        """
        if key is None:
            if hasattr(func, "__name__"):
                key = func.__name__
            elif hasattr(func, "name"):
                key = func.name
            else:
                raise ValueError(f"Invalid tool: {func}")

        # Add to _base_functions
        self._base_functions[key] = func

        return self

    def remove_tool(self, key: str) -> bool:
        """
        Remove a tool from the agent.

        Args:
            key: The name of the tool to remove.

        Returns:
            bool: True if the tool was found and removed, False otherwise.
        """
        if key in self._base_functions:
            del self._base_functions[key]
            return True

        return False

    async def toolset(self, toolset: Union["ToolSet", "ToolProvider"]) -> "Agent":
        """Add a toolset to the agent (supports both ToolSet and ToolProvider)

        Behavior:
        - ToolSet: Automatically wrapped in LocalProvider for unified provider-based routing
        - Named ToolProvider: Dynamic routing - tools retrieved on-demand

        Args:
            toolset: Either a ToolSet instance (will be wrapped in LocalProvider)
                    or a ToolProvider instance (used directly)

        Returns:
            The agent instance
        """
        # Import here to avoid circular imports
        from .providers import LocalProvider

        if isinstance(toolset, ToolSet):
            # Wrap ToolSet in LocalProvider for unified provider-based routing
            provider = LocalProvider(toolset)
            await provider.initialize()
            self.providers[provider.toolset_name] = provider
            logger.debug(
                f"Agent '{self.name}': Wrapped ToolSet in LocalProvider and added as provider (dynamic routing)"
            )
        elif isinstance(toolset, ToolProvider):
            # ToolProvider (remote or local) - dynamic routing
            name = getattr(toolset, "toolset_name", None)
            if not isinstance(name, str) or not name:
                raise ValueError("A toolset provider must have a nonempty toolset_name")
            self.providers[name] = toolset
            logger.debug(
                f"Agent '{self.name}': Added {type(toolset).__name__} (dynamic routing)"
            )
        else:
            raise TypeError(
                f"Invalid toolset type: {type(toolset)}. "
                f"Expected ToolSet or ToolProvider instance."
            )

        return self

    # ===== New Provider-Based Tool Management =====

    async def mcp(self, name: str, provider: "ToolProvider") -> "Agent":
        """Add a MCP provider to the agent (one at a time)

        Dynamic routing approach: Provider tools are retrieved on-demand via get_tools_for_llm()
        and called via call_tool() with prefix routing. No pre-wrapping.

        Args:
            name: Name/identifier for this provider (e.g., 'biomcp')
            provider: ToolProvider instance (MCPProvider recommended, already initialized)

        Returns:
            The agent instance
        """
        # Store the provider for dynamic routing
        self.providers[name] = provider

        logger.debug(
            f"Agent '{self.name}': Added MCP provider '{name}' (dynamic routing)"
        )

        return self

    async def _list_provider_tools(self):
        """Discover remote App metadata concurrently, preserving provider order.

        MCP clients can be task-bound; only ToolSetProvider gets a separate
        task. Every started task is drained even if the caller is cancelled.
        """
        from .providers import ToolSetProvider

        providers = list(self.providers.items())
        remote_tasks = {
            name: asyncio.create_task(provider.list_tools())
            for name, provider in providers
            if isinstance(provider, ToolSetProvider)
        }
        try:
            for name, provider in providers:
                try:
                    tools = await remote_tasks[name] if name in remote_tasks else await provider.list_tools()
                except Exception as e:
                    logger.warning(f"Failed to list tools from provider '{name}': {e}")
                    continue
                yield name, provider, tools
        finally:
            for task in remote_tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*remote_tasks.values(), return_exceptions=True)

    async def get_tools_for_llm(self) -> list[dict]:
        """Get all tools for LLM - includes _base_functions and provider tools

        Dynamically retrieves tools from providers (utilizing their caching mechanisms).
        Provider tools are prefixed with {provider_name}_ to distinguish them from agent's own tools.

        Returns:
            List of tool definitions in OpenAI format
        """
        # 1. Get tools from _base_functions (Agent's own tools - no prefix)
        base_tools = self._convert_functions(relaxed_schema=self.relaxed_schema)

        # 2. Get tools from providers (dynamic retrieval - uses provider caching)
        # Providers return ToolInfo with pre-generated inputSchema (the "function" part)
        logger.debug(f"get tools for llm: {self.providers} ")
        provider_tools = []
        # MCP provider tools are task-bound: the MCP client session lives on the
        # endpoint's main asyncio task, so they cannot be run via background_task
        # (a separate asyncio.Task) — cross-task access makes list_tools()/call_tool()
        # fail with "tool not found". Collect their fully-qualified names so step 3
        # does NOT advertise a `_background` option for them (foreground-only).
        from .providers import MCPProvider

        mcp_tool_names: set[str] = set()
        async for provider_name, provider, tools in self._list_provider_tools():
            is_mcp_provider = isinstance(provider, MCPProvider)
            try:
                # All providers must provide inputSchema as the complete "function" part
                for tool_info in tools:
                    # inputSchema must be present (design contract with providers)
                    assert tool_info.inputSchema is not None, (
                        f"Provider '{provider_name}': Tool '{tool_info.name}' missing inputSchema"
                    )

                    # Make a copy to avoid modifying cached data
                    function_schema = tool_info.inputSchema.copy()

                    # Add provider prefix to tool name (using __ as separator to support provider names with _)
                    full_name = f"{provider_name}__{tool_info.name}"
                    function_schema["name"] = full_name
                    if is_mcp_provider:
                        mcp_tool_names.add(full_name)

                    # Build complete OpenAI tool dict
                    provider_tools.append(
                        {
                            "type": "function",
                            "function": function_schema,
                        }
                    )

                logger.debug(
                    f"Agent '{self.name}': Added {len(tools)} tools from provider '{provider_name}'"
                )

            except Exception as e:
                logger.warning(
                    f"Agent '{self.name}': Failed to get tools from provider '{provider_name}': {e}"
                )

        # 3. Inject _background parameter into all eligible tool schemas
        _BG_PARAM_SKIP = {"background_task", "call_agent"}
        _BG_PARAM_SKIP_PREFIXES = ("call_agent_",)

        all_tools = base_tools + provider_tools
        for tool_dict in all_tools:
            name = tool_dict["function"]["name"]
            if (
                name in _BG_PARAM_SKIP
                or name in mcp_tool_names
                or any(name.startswith(p) for p in _BG_PARAM_SKIP_PREFIXES)
            ):
                continue

            func = tool_dict["function"]
            # Deep copy parameters to avoid mutating cached provider schemas
            if "parameters" in func:
                func["parameters"] = copy.deepcopy(func["parameters"])
            else:
                func["parameters"] = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                }

            func["parameters"]["properties"]["_background"] = {
                "type": "boolean",
                "description": (
                    "Set true to run in background without blocking. "
                    "You'll get a task_id to track progress via background_task()."
                ),
            }
            # All params must be in required for strict mode
            func["parameters"].setdefault("required", []).append("_background")

        if self.tool_exposure == "core":
            from .agent_core_tools import CORE_TOOLS, call_app_tool_schema, describe_app_schema

            from .agent_nodes import NODE_TOOLS, add_node_param

            all_tools = [t for t in all_tools if t["function"]["name"] in CORE_TOOLS
                         and t["function"]["name"] not in ("describe_app", "call_app_tool")]
            for tool_dict in all_tools:
                if tool_dict["function"]["name"] in NODE_TOOLS:
                    add_node_param(tool_dict)
            from .agent_app_directory import LibraryAppProvider

            index = await self._app_tool_index()
            descriptions = {name: provider.description for name, provider in self.providers.items()
                            if name in index and isinstance(provider, LibraryAppProvider)}
            all_tools += [describe_app_schema(), call_app_tool_schema(index, descriptions)]

        from .agent_model import stabilize_tool_definitions

        return stabilize_tool_definitions(all_tools)

    def _should_inject_context_variables(self, prefixed_name: str) -> bool:
        """Determine if context_variables should be injected for a tool.

        Returns True for:
        1. ToolSet base functions (has _is_tool attribute)
        2. ToolSetProvider calls
        3. Functions that explicitly declare context_variables parameter

        Args:
            prefixed_name: Tool name (possibly with prefix)

        Returns:
            bool: Whether to inject context_variables
        """
        # Check 1: Is it a ToolSet base function with _is_tool attribute?
        if prefixed_name in self._base_functions:
            func = self._base_functions[prefixed_name]
            if hasattr(func, "_is_tool"):
                return True
            else:
                # Check 3: Does the function explicitly declare context_variables parameter?
                try:
                    sig = inspect.signature(func)
                    if "context_variables" in sig.parameters:
                        return True
                except (ValueError, TypeError):
                    pass
                return False

        # Check 2: Is it a ToolSetProvider call (prefix routing)?
        if "__" in prefixed_name:
            provider_name = prefixed_name.split("__", 1)[0]
            if provider_name in self.providers:
                provider = self.providers[provider_name]
                # Check if it's a ToolSetProvider
                from .providers import ToolSetProvider, LocalProvider

                if isinstance(provider, (ToolSetProvider, LocalProvider)):
                    return True

        return False

    def _prepare_context_variables(
        self,
        prefixed_name: str,
        args: dict,
        context_variables: dict | None,
        tool_call_id: str | None,
    ) -> dict:
        """Inject context_variables into args if needed."""
        should_inject = self._should_inject_context_variables(prefixed_name)

        if not should_inject or context_variables is None:
            return args

        # Create _call_agent callback
        async def _call_agent_wrap(
            messages: list,
            system_prompt: str | None = None,
            model: str | None = None,
            use_memory: bool = False,
        ) -> dict:
            memory = self.memory[:-1] if use_memory else None
            context = get_current_run_context()
            active_model = get_current_run_model() if context is not None and context.agent is self else None
            preferred_model = active_model or (self.models[0] if self.models else None)
            if self.model_scope is not None:
                return await _call_agent(
                    messages=messages, system_prompt=system_prompt,
                    model=_resolve_model_spec_with_current_provider(
                        model or preferred_model, current_model=preferred_model,
                        model_scope=self.model_scope),
                    memory=memory, model_scope=self.model_scope,
                )
            return await _call_agent(
                messages=messages,
                system_prompt=system_prompt,
                model=_resolve_model_spec_with_current_provider(
                    model,
                    current_model=preferred_model,
                ),
                memory=memory,
            )

        context = get_current_run_context()
        inherited_model = get_current_run_model() if context is not None and context.agent is self else None
        caller_models = list(self.models)
        if inherited_model:
            caller_models = [inherited_model, *[
                candidate for candidate in caller_models
                if candidate != inherited_model
            ]]

        # Build complete context_variables with execution metadata
        full_context = context_variables.copy()
        full_context["agent_name"] = self.name
        if tool_call_id is not None:
            full_context["tool_call_id"] = tool_call_id
        full_context["_call_agent"] = _call_agent_wrap
        full_context["caller_models"] = caller_models  # For scfm_router LLM calls

        # Pre-inject output buffer for background task adoption on timeout
        if tool_call_id is not None:
            output_buffer: list[str] = []
            self._tool_output_buffers[tool_call_id] = output_buffer
            full_context["_report_output"] = lambda line, buf=output_buffer: buf.append(str(line))

        # Merge with existing context_variables in args
        if _CTX_VARS_NAME in args:
            existing = args[_CTX_VARS_NAME]
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(full_context)
                args[_CTX_VARS_NAME] = merged
            else:
                args[_CTX_VARS_NAME] = full_context
        else:
            args[_CTX_VARS_NAME] = full_context

        return args

    async def call_tool(
        self,
        prefixed_name: str,
        args: dict,
        context_variables: dict | None = None,
        tool_call_id: str | None = None,
    ) -> Any:
        """Call a tool by name with automatic routing and suffix matching fallback."""
        if prefixed_name == "file_manager__read_file":
            from .agent_tools import read_tool_output

            saved = read_tool_output(self, args)
            if saved is not None:
                return saved
        if self.tool_exposure == "core":
            from .agent_nodes import NODE_TOOLS, route

            if prefixed_name in NODE_TOOLS:
                # The conversation's working node, or one the call names.
                node = args.pop("node", "") or (context_variables or {}).get("chat_node") or ""
                if node:
                    routed = await route(self, prefixed_name, args, str(node))
                    if routed is not None:
                        return routed
        # Prepare context_variables if needed
        args = self._prepare_context_variables(
            prefixed_name, args, context_variables, tool_call_id
        )

        # print shrinked args
        short_args = f"{args}"[:100]
        logger.info(f"Calling tool {prefixed_name} | {short_args}")
        # Exact routes never probe unrelated providers. Validate an explicitly
        # qualified method against only its target's advertised whitelist; a
        # missing method must not suffix-match a different provider.
        all_tools: dict[str, str] = dict.fromkeys(self._base_functions, "base")
        resolved_name = None
        if prefixed_name in self._base_functions:
            resolved_name = prefixed_name
        elif "__" in prefixed_name and prefixed_name.split("__", 1)[0] in self.providers:
            provider_name, tool_name = prefixed_name.split("__", 1)
            tools = await self.providers[provider_name].list_tools()
            if not any(tool.name == tool_name for tool in tools):
                raise ValueError(f"Tool '{tool_name}' not found in provider '{provider_name}'")
            all_tools[prefixed_name] = provider_name
            resolved_name = prefixed_name
        elif getattr(self, "_explicit_tool_bindings", False):
            raise ValueError("Tool is not available in the explicit Agent bindings")
        else:
            # Legacy unqualified/suffix names retain registration-order priority.
            async for provider_name, _, tools in self._list_provider_tools():
                for tool_info in tools:
                    all_tools[f"{provider_name}__{tool_info.name}"] = provider_name
            for name in all_tools:
                if name.endswith(prefixed_name):
                    resolved_name = name
                    logger.warning(
                        f"Tool '{prefixed_name}' resolved to '{resolved_name}' via suffix matching"
                    )
                    break

        if not resolved_name:
            raise ValueError(
                f"Tool '{prefixed_name}' not found. Available tools: {list(all_tools.keys())[:10]}..."
            )

        # 3. Call the resolved tool
        from .background import _bg_report

        source = all_tools[resolved_name]
        _bg_report(f"[tool] Calling {resolved_name}...")
        try:
            if source == "base":
                func = self._base_functions[resolved_name]
                result = await run_func(func, **args)
            else:
                # Provider tool: source is the provider_name
                provider = self.providers[source]
                tool_name = resolved_name.split("__", 1)[1]
                result = await provider.call_tool(tool_name, args)
            _bg_report(f"[tool] {resolved_name} completed")
        except Exception as e:
            _bg_report(f"[tool] {resolved_name} failed: {e}")
            logger.error(
                f"Failed to call tool '{resolved_name}' (source: {source}): {e}"
            )
            raise

        return result

    def _convert_functions(self, relaxed_schema: bool) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            desc = parse_func(func, update_by_docstring=False)

            assert isinstance(desc.name, str), "Function name must be a string"

            skip_params = list(_SKIP_PARAMS)

            func_dict = desc_to_openai_dict(
                desc,
                skip_params=skip_params,
                relaxed_schema=relaxed_schema,
            )
            functions.append(func_dict)

        return functions

    async def _handle_tool_calls(
        self,
        tool_calls: list,
        context_variables: dict,
        timeout: float,
        time_delta: float = 0.5,
        check_stop: Callable | None = None,
    ) -> list[dict]:
        """Run one turn's tool calls (pantheon/agent_tools.py)."""
        from .agent_tools import execute_tool_calls

        return await execute_tool_calls(self, tool_calls, context_variables, timeout, time_delta, check_stop)

    async def _acompletion(
        self,
        messages: list[dict],
        model: str,
        tool_use: bool = True,
        response_format: Any | None = None,
        process_chunk: Callable | None = None,
        context_variables: dict | None = None,
    ) -> dict:
        """One model call (pantheon/agent_model.py)."""
        from .agent_model import complete

        return await complete(self, messages, model, tool_use, response_format, process_chunk, context_variables)

    async def _acompletion_with_models(
        self,
        history,
        tool_use=True,
        response_format=None,
        process_chunk=None,
        model: str | list[str] | None = None,
        context_variables: dict | None = None,
    ):
        """One model call on the first model of the chain that answers."""
        from .agent_model import complete_models

        return await complete_models(self, history, tool_use, response_format, process_chunk,
                                     model=model, context_variables=context_variables)

    def _render_system_prompt(self, prompt: str, context_variables: dict) -> str:
        """Render system prompt — delegates to pantheon.internal.system_prompt."""
        from pantheon.internal.system_prompt import render_system_prompt
        return render_system_prompt(prompt, context_variables)

    async def _run_stream(
        self,
        messages: list[dict],
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        check_stop: Callable | None = None,
        max_turns: int | float = float("inf"),
        context_variables: dict | None = None,
        response_format: Any | None = None,
        tool_use: bool = True,
        tool_timeout: int | None = None,
        model: str | list[str] | None = None,
        execution_context_id: str | None = None,
    ) -> ResponseDetails:
        """Run the agent loop (pantheon/agent_loop.py) on a conversation."""
        response_format = response_format or self.response_format
        history = copy.deepcopy(messages)
        # The model reads assembled user messages (_llm_content).
        for msg in history:
            if "_llm_content" in msg:
                msg["content"] = msg["_llm_content"]

        from .utils.vision import expand_image_references_for_llm

        if self._image_resolver is None:
            history = expand_image_references_for_llm(history)
        # A model that cannot see images reads a description of the user's
        # images instead. Fail-open: never break the run.
        try:
            from .utils.vision_downgrade import downgrade_blind_user_images

            primary = get_current_run_model() or (self.models[0] if self.models else None)
            if self._image_resolver is None and not (primary or '').startswith(('fleet-model://', 'fleet-route://')):
                history = await downgrade_blind_user_images(history, primary)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"vision downgrade skipped: {e}")

        system = {
            "role": "system",
            "content": self._render_system_prompt(self.instructions, context_variables or {}),
            "timestamp": time.time(),
            "id": history[0].get("id") if history and history[0]["role"] == "system" else None,
        }
        system["id"] = system["id"] or str(uuid4())
        if execution_context_id is not None:
            system["execution_context_id"] = execution_context_id
        if history and history[0]["role"] == "system":
            history[0] = {**history[0], **system}
        else:
            history.insert(0, system)

        Response = create_model("Response", result=(response_format, ...)) if response_format else None
        context_variables = context_variables or {}
        init_len = await run_turns(
            self, history,
            process_chunk=process_chunk,
            process_step_message=process_step_message,
            check_stop=check_stop,
            max_turns=max_turns,
            context_variables=context_variables,
            response_format=Response,
            tool_use=tool_use,
            tool_timeout=tool_timeout or self.tool_timeout,
            model=model,
            execution_context_id=execution_context_id,
        )
        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
            execution_context_id=execution_context_id,
        )

    async def _input_to_openai_messages(
        self,
        msg: AgentInput,
    ) -> list[dict]:
        """Convert input to OpenAI message format.

        Handles conversion of user input types to standardized message dicts:
        - VisionInput: Converts vision input to messages
        - BaseModel: Converts to JSON user message
        - str: Wraps in user message dict
        - list: Processes each item and accumulates messages

        Args:
            msg: The input message

        Returns:
            List of message dicts in OpenAI format
        """
        # Lazy import to avoid loading PIL/numpy at module load time
        from .utils.vision import VisionInput, vision_to_openai
        
        assert isinstance(
            msg, (list, str, BaseModel, AgentResponse, VisionInput, dict)
        ), (
            "Message must be a list, string, BaseModel, AgentResponse, VisionInput, or dict"
        )
        if isinstance(msg, AgentResponse):
            # For accepting the result of previous run or other agent
            msg = msg.content

        # Convert message to OpenAI message format based on type
        if isinstance(msg, dict):
            # Handle dict messages (e.g., tool_message from team.run() loop)
            # These are already in OpenAI message format
            if msg.get("role") == "tool":
                # Tool message from sub-agent completion
                converted_messages = [msg]
            else:
                # Regular dict message, wrap as user message
                converted_messages = [_create_user_message(msg)]
        elif isinstance(msg, VisionInput):
            # Vision input: use specialized converter
            converted_messages = vision_to_openai(msg)

        elif isinstance(msg, BaseModel):
            # BaseModel input: convert to JSON user message
            converted_messages = [_create_user_message(msg.model_dump_json())]

        elif isinstance(msg, str):
            # String input: wrap in user message
            converted_messages = [_create_user_message(msg)]

        elif isinstance(msg, list):
            # List input: process each item and accumulate
            converted_messages = []
            for item in msg:
                if isinstance(item, str):
                    # String item: create user message
                    converted_messages.append(_create_user_message(item))
                elif isinstance(item, VisionInput):
                    # Vision input: extend with converted vision messages
                    converted_messages.extend(vision_to_openai(item))
                elif isinstance(item, BaseModel):
                    # BaseModel item: convert to JSON user message
                    converted_messages.append(
                        _create_user_message(item.model_dump_json())
                    )
                else:
                    # Dict item: use as-is
                    assert isinstance(item, dict), (
                        "Message must be a string, BaseModel or dict"
                    )
                    converted_messages.append(item)

        return converted_messages

    async def _prepare_execution_context(
        self,
        msg: AgentInput,
        execution_context_id: str | None = None,
        context_variables: dict | None = None,
        memory: "Memory | None" = None,
        use_memory: bool | None = None,
    ) -> ExecutionContext:
        """The run's conversation: the stored history (when memory is used)
        followed by the input converted to messages."""
        # Determine whether to use memory
        should_use_memory = use_memory if use_memory is not None else self.use_memory
        memory_instance = memory or self.memory
        working_context_variables = (context_variables or {}).copy()
        fork_context_messages = working_context_variables.pop(
            "_cache_safe_fork_context_messages",
            None,
        )

        input_messages = await self._input_to_openai_messages(msg)

        # Process images: convert Base64 to file:// paths for efficient storage
        image_store = self._image_store()
        chat_id = memory_instance.id if memory_instance else "default"
        for m in input_messages:
            image_store.process_message_images(m, chat_id, normalize_paths=self._image_resolver is None)

        # The stored conversation; the model reads it from its latest compaction
        # on (pantheon/agent_context.py).
        conversation_history = []
        if should_use_memory and memory_instance:
            from .agent_context import trim_to_view

            conversation_history = trim_to_view(memory_instance.get_messages(
                execution_context_id=execution_context_id, for_llm=False))
        if isinstance(fork_context_messages, list) and fork_context_messages:
            conversation_history = [
                *copy.deepcopy(fork_context_messages),
                *conversation_history,
            ]
        conversation_history += input_messages
        conversation_history = self._sanitize_messages(conversation_history)

        # preserve execution_context_id if tool need
        context_variables = working_context_variables

        # Inject this Agent composition's configured context variables.
        context_variables.update(self._settings().get_context_variables())

        if execution_context_id is not None:
            context_variables["execution_context_id"] = execution_context_id
        # preserve client_id from context_variables or memory
        context_variables[_CLIENT_ID_NAME] = context_variables.get(
            _CLIENT_ID_NAME,
            memory_instance.id,
        )

        return ExecutionContext(
            conversation_history=conversation_history,
            context_variables=context_variables or {},
            execution_context_id=execution_context_id,
            should_use_memory=should_use_memory,
            memory_instance=memory_instance,
            input_messages=input_messages,  # NEW: Pass converted messages for memory update
        )

    async def run(
        self,
        msg: AgentInput,
        response_format: Any | None = None,
        tool_use: bool = True,
        context_variables: dict | None = None,
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        check_stop: Callable | None = None,
        memory: "Memory | None" = None,
        use_memory: bool | None = None,
        update_memory: bool = True,
        tool_timeout: int | None = None,
        model: str | list[str] | None = None,
        execution_context_id: str | None = None,
        max_turns: int | float = float("inf"),
    ) -> AgentResponse:
        """Run the agent.

        Args:
            msg: The input message to the agent.
            response_format: The response format to use.
            tool_use: Whether to use tools.
            context_variables: Runtime variables available to tools during execution.
                These are stored in ExecutionContext.runtime_context and injected
                into tools that request them via the 'context_variables' parameter.
                Example: {'user_id': '123', 'session_id': 'abc'}
            process_chunk: The function to process the chunk.
            process_step_message: The function to process the step message.
            check_stop: The function to check if the agent should stop.
            memory: The memory to use.
            use_memory: Whether to use short term memory.
            update_memory: Whether to update the short term memory.
            tool_timeout: The timeout for the tool.
            model: The model to use in this run.
                Could be a list of models for fallback.
                If not provided, the model will be selected from the agent's models.
            execution_context_id: Unique identifier for sub-agent delegation contexts.
                Used for message filtering in _run_stream to isolate messages by
                delegation context. None for primary/team agents.

        Returns:
            The agent response.
            If the agent is interrupted, the AgentResponse will have the interrupt flag set to True.
        """
        # The run's conversation: stored history plus the input.
        exec_context = await self._prepare_execution_context(
            msg,
            execution_context_id=execution_context_id,
            context_variables=context_variables,
            memory=memory,
            use_memory=use_memory,
        )
        # Prepare response format
        response_format_to_use = response_format or self.response_format

        # ============ Unified message processing: Always detect attachments ============
        async def _process_step_message(step_message: dict):
            # Validate message before processing (prevent empty messages in memory)
            role = step_message.get("role")
            if role is None or (isinstance(role, str) and not role.strip()):
                logger.warning(
                    f"Dropping invalid message without role: {step_message}"
                )
                return  # Skip invalid message
            
            # Get execution_context_id from ExecutionContext
            if (
                exec_context.execution_context_id is not None
                and "execution_context_id" not in step_message
            ):
                step_message["execution_context_id"] = exec_context.execution_context_id

            await _detect_attachments(step_message)

            if update_memory and exec_context.memory_instance:
                exec_context.memory_instance.add_messages([step_message])

            if process_step_message is not None:
                try:
                    await run_func(process_step_message, step_message)
                except Exception as e:
                    logger.error(f"Error in process_step_message: {e}")

        async def _process_chunk(chunk: dict):
            if (
                exec_context.execution_context_id is not None
                and "execution_context_id" not in chunk
            ):
                chunk["execution_context_id"] = exec_context.execution_context_id
            if process_chunk is not None:
                try:
                    await run_func(process_chunk, chunk)
                except Exception as e:
                    logger.error(f"Error in process_chunk: {e}")

        # now user input also goes through the pipeline
        # The input is stored and shown like every later step of the run.
        for msg in exec_context.input_messages or []:
            await _process_step_message(msg)

        try:
            run_context = AgentRunContext(
                agent=self,
                memory=exec_context.memory_instance,
                execution_context_id=exec_context.execution_context_id,
                process_step_message=_process_step_message,
                process_chunk=_process_chunk,
            )
            token = _RUN_CONTEXT.set(run_context)
            run_result = await self._run_stream(
                messages=exec_context.conversation_history,
                response_format=response_format_to_use,
                tool_use=tool_use,
                context_variables=exec_context.context_variables,
                process_chunk=_process_chunk,
                process_step_message=_process_step_message,
                check_stop=check_stop,
                tool_timeout=tool_timeout,
                model=model,
                execution_context_id=exec_context.execution_context_id,
                max_turns=max_turns,
            )
        except StopRunning as e:
            logger.info("StopRunning")
            partial_content = ""
            if update_memory and exec_context.memory_instance:
                # Save partial assistant message if the LLM was mid-stream
                partial = e.partial_message
                if partial and isinstance(partial, dict):
                    content = partial.get("content")
                    if content and str(content).strip():
                        partial.setdefault("role", "assistant")
                        partial["agent_name"] = self.name
                        exec_context.memory_instance.add_messages([partial])
                        partial_content = str(content)
                exec_context.memory_instance.cleanup()
            return AgentResponse(
                agent_name=self.name,
                content=partial_content,
                details=None,
                interrupt=True,
            )
        finally:
            if "token" in locals():
                _RUN_CONTEXT.reset(token)

        last_message = run_result.messages[-1]
        content = last_message.get("parsed") if response_format_to_use else last_message.get("content")
        return AgentResponse(agent_name=self.name, content=content, details=run_result)

    def setup_bg_notify_queue(self, queue: asyncio.Queue):
        """Wire bg task completion to an external asyncio.Queue.

        For frontends that manage their own event loops (e.g. the REPL with
        prompt_toolkit).

        Args:
            queue: The asyncio.Queue to push notification strings into.
        """
        def _on_complete(bg_task):
            status = bg_task.status
            result_preview = ""
            if bg_task.result is not None:
                result_preview = str(bg_task.result)[:200]
            elif bg_task.error:
                result_preview = bg_task.error[:200]
            notif = (
                f"[Background task '{bg_task.task_id}' ({bg_task.tool_name}) "
                f"{status}. Result: {result_preview}]"
            )
            try:
                queue.put_nowait(notif)
            except Exception:
                pass
        self._bg_manager.on_complete = _on_complete

    # FIX: agent should not call REPL, REPL call agent instead
    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        from .repl.core import Repl

        repl = Repl(self)
        await repl.run(message)

# ===== Utility Functions =====


def _create_user_message(content: str) -> dict:
    """Create a standard user message dict."""
    return {
        "role": "user",
        "content": content,
        "timestamp": time.time(),
        "id": str(uuid4()),
    }


async def _detect_attachments(step_message: dict) -> None:
    """Helper: Detect attachments in a message (independent of memory saving)."""
    # Attachment detection is currently disabled
    return


async def _call_agent(
    messages: list,
    system_prompt: Optional[str],
    model: Optional[str | list[str]] = None,
    memory: "Memory | None" = None,
    *, model_scope=None,
) -> dict:
    """call agent callback to let toolset use llm agent to sample response

    Returns:
        dict with keys:
        - success: bool
        - response: str (if success)
        - error: str (if not success)
        - _metadata: dict with cost info (if success)
            - current_cost: float - the cost of this nested LLM call
    """
    from .background import _bg_report, _bg_output_buffer

    context = get_current_run_context()
    # A delayed callback can run while another Agent is active. Only borrow its
    # selected model when it belongs to the same explicitly authorized scope.
    current_run_model = (get_current_run_model() if model_scope is None or
        getattr(getattr(context, 'agent', None), 'model_scope', None) is model_scope else None)
    inherited_model = _resolve_model_spec_with_current_provider(
        model or current_run_model,
        current_model=current_run_model,
        model_scope=model_scope,
    )

    # Progress callback for background context: reports each sub-agent message
    progress_cb = None
    if _bg_output_buffer.get() is not None:
        async def _on_step_message(msg):
            role = msg.get("role", "")
            content = str(msg.get("content", "") or "")
            if role == "assistant" and content:
                preview = content[:300]
                if len(content) > 300:
                    preview += "..."
                _bg_report(f"[agent] {preview}")
            elif role == "tool":
                tool_name = msg.get("name", msg.get("tool_call_id", "tool"))
                _bg_report(f"[agent] Tool result from {tool_name}")
        progress_cb = _on_step_message

    # not tested with remote mode, should work naturally with reverse call support
    try:
        # Create temporary Agent
        agent = Agent(
            name="sampler",
            model_scope=model_scope,
            model=inherited_model,
            instructions=system_prompt or "You are a helpful assistant.",
            memory=memory,
        )

        _bg_report(f"[agent] Sub-agent starting (model={inherited_model or 'default'})")

        # Run Agent with the user query
        result = await agent.run(
            messages,
            use_memory=False,
            update_memory=False,
            process_step_message=progress_cb,
        )

        _bg_report("[agent] Sub-agent completed")

        # Extract cost from the agent response
        nested_cost = 0.0
        if result and result.details and result.details.messages:
            # Find the last assistant message which contains cost info
            for msg in reversed(result.details.messages):
                if msg.get("role") == "assistant" and "_metadata" in msg:
                    nested_cost = msg.get("_metadata", {}).get("current_cost", 0.0)
                    break

        return {
            "success": True,
            "response": result.content,
            "_metadata": {
                "current_cost": nested_cost,
            },
        }

    except Exception as e:
        _bg_report(f"[agent] Sub-agent failed: {e}")
        # log stack trace
        logger.opt(exception=True).info("Error in agent sampling: {}", e)
        return {
            "success": False,
            "error": str(e),
        }
