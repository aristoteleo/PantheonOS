import asyncio
import copy
import inspect
import json
import random
import re
import sys
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, Union
from uuid import uuid4


from funcdesc import parse_func
from pydantic import BaseModel, create_model

if TYPE_CHECKING:
    from .internal.memory import Memory
    from .remote import (
        RemoteBackendFactory,
        RemoteConfig,
        RemoteWorker,
    )

from .toolset import ToolSet
from .utils.llm import (
    TimingTracker,
    process_messages_for_hook_func,
    process_messages_for_model,
    count_tokens_in_messages,
    format_token_visualization,
    process_tool_result,
)
from .utils.llm_providers import (
    call_llm_provider,
    create_enhanced_process_chunk,
    detect_provider,
    get_api_key_for_provider,
    get_base_url,
)
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
            ``+think:low``/``+think:medium``/``+think:high``.

    Returns:
        (clean_model_str, thinking_level) where *thinking_level* is
        ``"low"``, ``"medium"``, ``"high"``, or ``None``.
    """
    import re

    match = re.search(r"\+think(?::(\w+))?$", model_str)
    if not match:
        return model_str, None
    level = match.group(1) or "high"
    if level not in ("low", "medium", "high"):
        return model_str, None
    return model_str[: match.start()], level


def _is_model_tag(model_str: str) -> bool:
    """Check if a string is a model tag vs a model name.

    Model tags are quality/capability identifiers like "high", "normal,vision".
    Model names are actual model identifiers like "openai/gpt-4o", "gpt-4o-mini".
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
) -> str | list[str] | None:
    """Resolve model tags while preferring the current dialog provider.

    Example: if the active dialog runs on ``codex/gpt-5.4-mini`` and an
    internal helper asks for ``low``, keep the helper on the Codex provider
    instead of letting global auto-selection jump to OpenAI.
    """
    model = _normalize_model_spec(model)
    if not isinstance(model, str) or not _is_model_tag(model):
        return model

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


_CTX_VARS_NAME = "context_variables"
_SKIP_PARAMS = [_CTX_VARS_NAME]
_CLIENT_ID_NAME = "client_id"


class AgentService:
    def __init__(self, agent: "Agent", **kwargs):
        self.agent = agent
        
        from .remote import RemoteBackendFactory, RemoteWorker
        self.backend = RemoteBackendFactory.create_backend()
        self.worker: RemoteWorker = self.backend.create_worker(**kwargs)
        self.setup_worker()

    async def response(self, msg, **kwargs):
        resp = await self.agent.run(msg, **kwargs)
        return resp

    async def get_info(self):
        return {
            "name": self.agent.name,
            "instructions": self.agent.instructions,
            "models": self.agent.models,
            "tool_names": list(
                self.agent.functions.keys()
            ),  # Simplified: return tool names
        }

    async def get_message_queue(self):
        return await self.agent.events_queue.get()

    async def check_message_queue(self):
        # check if there is a message in the queue
        return not self.agent.events_queue.empty()

    async def add_tool(self, func: Callable):
        self.agent.tool(func)
        return {"success": True}

    def setup_worker(self):
        """Register methods with the worker"""
        self.worker.register(self.response)
        self.worker.register(self.get_info)
        self.worker.register(self.get_message_queue)
        self.worker.register(self.check_message_queue)
        self.worker.register(self.add_tool)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level=log_level)

        return await self.worker.run()


class RemoteAgent:
    def __init__(
        self,
        service_id_or_name: str,
        backend_config: Optional["RemoteConfig"] = None,
        **remote_kwargs,
    ):
        self.service_id_or_name = service_id_or_name
        from .remote import RemoteBackendFactory

        self.backend = RemoteBackendFactory.create_backend(backend_config)
        self.remote_kwargs = remote_kwargs
        self.name = None
        self.instructions = None
        self.model = None
        self.models = []  # Initialize empty list to avoid AttributeError
        self.events_queue = RemoteAgentMessageQueue(self)

    async def _connect(self):
        return await self.backend.connect(self.service_id_or_name, **self.remote_kwargs)

    async def fetch_info(self):
        service = await self._connect()
        info = await service.invoke("get_info")
        self.name = info["name"]
        self.instructions = info["instructions"]
        self.models = info["models"]
        self.tool_names = info.get("tool_names", [])  # New format
        # Backward compatibility
        if "functions_names" in info:
            self.tool_names = info["functions_names"]
        await service.close()
        return info

    async def run(self, msg: "AgentInput", **kwargs):
        await self.fetch_info()
        service = await self._connect()
        try:
            return await service.invoke("response", {"msg": msg, **kwargs})
        finally:
            await service.close()

    async def tool(self, func: Callable):
        service = await self._connect()
        try:
            func_arg = {"func": func}
            await service.invoke("add_tool", func_arg)
        finally:
            await service.close()

    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        await self.fetch_info()
        from .repl.core import Repl

        repl = Repl(self)
        await repl.run(message)


class RemoteAgentMessageQueue:
    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    async def get(self, interval: float = 0.2):
        service = await self.agent._connect()
        try:
            while True:
                res = await service.invoke("check_message_queue")
                if res:
                    return await service.invoke("get_message_queue")
                await asyncio.sleep(interval)
        finally:
            await service.close()


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


class AgentTransfer(BaseModel):
    """
    The AgentTransfer class is used to transfer the agent response to another agent.
    """

    from_agent: str
    to_agent: str
    history: list[dict]
    context_variables: dict
    init_message_length: int
    tool_call_id: str | None = None


AgentInput = Union[
    str,
    BaseModel,
    AgentResponse,
    list[str | BaseModel | dict],
    AgentTransfer,
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


def _is_retryable_error(error: Exception) -> bool:
    """Determine if an LLM API error is transient and worth retrying."""
    from pantheon.utils.adapters.base import (
        ServiceUnavailableError,
        InternalServerError,
        RateLimitError,
        APIConnectionError,
    )
    if isinstance(error, (ServiceUnavailableError, InternalServerError,
                          RateLimitError, APIConnectionError)):
        return True
    # Fallback: string matching for common transient error indicators
    error_str = str(error).lower()
    return any(kw in error_str for kw in (
        "overloaded", "rate", "capacity", "temporarily", "503", "502", "429",
    ))


def _get_message_text(message: dict) -> str | None:
    """Extract text content from a message (handles string or multimodal list)."""
    # Use _llm_content if available (already modified), otherwise use content
    user_input = message.get("_llm_content")
    if user_input is None:
        user_input = message.get("content")
    
    if not user_input:
        return None
        
    if isinstance(user_input, str):
        return user_input
        
    if isinstance(user_input, list):
        # Extract text from content array
        text_parts = [
            part.get("text", "") 
            for part in user_input 
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return " ".join(text_parts)
        
    return None


def _apply_injections(message: dict, injections: list[dict]) -> None:
    """Apply collected injections to the message."""
    if not injections:
        return

    # Initialize _llm_content if needed
    if "_llm_content" not in message or message["_llm_content"] is None:
        message["_llm_content"] = message.get("content")
    
    # Append all injections
    for injection in injections:
        content_str = injection["content"]
        source = injection["source"]
        
        if isinstance(message["_llm_content"], str):
            message["_llm_content"] += f"\n\n{content_str}"
        elif isinstance(message["_llm_content"], list):
            # For content arrays, append as text part
            message["_llm_content"].append({
                "type": "text",
                "text": f"\n\n{content_str}"
            })
        
        logger.debug(
            f"Injected {len(content_str)} chars from {source}"
        )



def think(thought: str) -> str:
    """Use this tool to think step-by-step before acting. It records your reasoning without taking any action or obtaining new information. Use it to analyze tool outputs, plan multi-step approaches, verify compliance with instructions, or reconsider your strategy."""
    return "Thought recorded."


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
        tool_timeout: The timeout for the tool. (default: from settings.endpoint.local_toolset_timeout, or 3600s)
        relaxed_schema: Use relaxed (non-strict) tool schema mode. (default: False)
        max_tool_content_length: The maximum length of the tool content. (default: 100000)
        description: The description of the agent. (default: None)
        think_tool: Whether to enable the think tool for structured reasoning. (default: False)
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
        think_tool: bool = False,
    ):
        # Parse +think suffix before any processing
        thinking_level: str | None = None
        if isinstance(model, str):
            model, thinking_level = _parse_thinking_suffix(model)

        model = _normalize_model_spec(model)
        self.id = uuid4()
        self.name = name
        self.instructions = instructions
        self.description = description
        self._model_was_explicit = model is not None

        # Smart model selection: use ModelSelector when no model specified
        if model is None:
            # Get default model fallback chain from ModelSelector
            self.models = _get_default_model()
        elif isinstance(model, str):
            # Check if it's a tag string (e.g., "high", "normal,vision")
            if _is_model_tag(model):
                # Resolve tag to model fallback chain
                self.models = _resolve_model_tag(model)
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

        if think_tool:
            self.tool(think, key="think")

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
        # Input queue for run_loop() — messages/notifications enter here
        self.input_queue: asyncio.Queue = asyncio.Queue()
        self._loop_running: bool = False
        self.relaxed_schema = relaxed_schema
        self.icon = icon

        # Provider management (MCP, ToolSet, etc.)
        self.providers: dict[str, ToolProvider] = {}  # name -> ToolProvider instance
        self.not_loaded_toolsets: list[str] = []  # Track which toolsets failed to load

        # Context injectors for dynamic content injection
        self.context_injectors: list = []  # List of ContextInjector instances

        # Background task support
        from .background import BackgroundTaskManager

        self._bg_manager = BackgroundTaskManager()
        self._tool_output_buffers: dict[str, list[str]] = {}
        self._register_bg_tools()

        # Callable hooks registered by plugins via get_toolsets() (set during PantheonTeam.async_setup)
        # Each hook is a plain async callable — agent has no knowledge of plugins.
        # Signature: async (history: list[dict], context_variables: dict) -> list[dict]
        self._ephemeral_hooks: list = []
        # Signature: async (tool_calls: list[dict], tool_messages: list[dict], context_variables: dict) -> None
        self._tool_tracking_hooks: list = []

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

    def _get_tool_timeout(self) -> int:
        """Get tool timeout with priority: user override > settings."""
        if self._tool_timeout_override is not None:
            return self._tool_timeout_override
        try:
            from .settings import get_settings
            return get_settings().tool_timeout
        except Exception:
            return 3600

    def _get_max_tool_content_length(self) -> int:
        """Get max tool content length with priority: user override > settings."""
        if self._max_tool_content_length_override is not None:
            return self._max_tool_content_length_override
        try:
            from .settings import get_settings
            return get_settings().max_tool_content_length
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

    def _filter_messages_by_execution_context(
        messages: list[dict], execution_context_id: str | None
    ) -> list[dict]:
        if not messages:
            return []
        if execution_context_id is None:
            return [msg for msg in messages if msg.get("execution_context_id") is None]
        return [
            msg
            for msg in messages
            if msg.get("execution_context_id") == execution_context_id
        ]

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
        - ToolProvider (LocalProvider/ToolSetProvider): Dynamic routing - tools retrieved on-demand

        Args:
            toolset: Either a ToolSet instance (will be wrapped in LocalProvider)
                    or a ToolProvider instance (used directly)

        Returns:
            The agent instance
        """
        # Import here to avoid circular imports
        from .providers import LocalProvider, ToolSetProvider

        if isinstance(toolset, ToolSet):
            # Wrap ToolSet in LocalProvider for unified provider-based routing
            provider = LocalProvider(toolset)
            await provider.initialize()
            self.providers[provider.toolset_name] = provider
            logger.debug(
                f"Agent '{self.name}': Wrapped ToolSet in LocalProvider and added as provider (dynamic routing)"
            )
        elif isinstance(toolset, (ToolSetProvider, LocalProvider)):
            # ToolProvider (remote or local) - dynamic routing
            self.providers[toolset.toolset_name] = toolset
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

    async def get_tools_for_llm(self) -> list[dict]:
        """Get all tools for LLM - includes _base_functions and provider tools

        Dynamically retrieves tools from providers (utilizing their caching mechanisms).
        Provider tools are prefixed with {provider_name}_ to distinguish them from agent's own tools.

        Returns:
            List of tool definitions in OpenAI format
        """
        # 1. Get tools from _base_functions (Agent's own tools - no prefix)
        base_tools = self._convert_functions(
            relaxed_schema=self.relaxed_schema, allow_transfer=True
        )

        # 2. Get tools from providers (dynamic retrieval - uses provider caching)
        # Providers return ToolInfo with pre-generated inputSchema (the "function" part)
        logger.debug(f"get tools for llm: {self.providers} ")
        provider_tools = []
        for provider_name, provider in self.providers.items():
            try:
                # Get tools from provider (uses cached list if available)
                tools = await provider.list_tools()

                # All providers must provide inputSchema as the complete "function" part
                for tool_info in tools:
                    # inputSchema must be present (design contract with providers)
                    assert tool_info.inputSchema is not None, (
                        f"Provider '{provider_name}': Tool '{tool_info.name}' missing inputSchema"
                    )

                    # Make a copy to avoid modifying cached data
                    function_schema = tool_info.inputSchema.copy()

                    # Add provider prefix to tool name (using __ as separator to support provider names with _)
                    function_schema["name"] = f"{provider_name}__{tool_info.name}"

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
        _BG_PARAM_SKIP = {"background_task", "think", "call_agent"}
        _BG_PARAM_SKIP_PREFIXES = ("transfer_to_", "call_agent_")

        all_tools = base_tools + provider_tools
        for tool_dict in all_tools:
            name = tool_dict["function"]["name"]
            if name in _BG_PARAM_SKIP or any(
                name.startswith(p) for p in _BG_PARAM_SKIP_PREFIXES
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

        from pantheon.utils.token_optimization import stabilize_tool_definitions

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
            preferred_model = get_current_run_model() or (self.models[0] if self.models else None)
            return await _call_agent(
                messages=messages,
                system_prompt=system_prompt,
                model=_resolve_model_spec_with_current_provider(
                    model,
                    current_model=preferred_model,
                ),
                memory=memory,
            )

        inherited_model = get_current_run_model()
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
        # Prepare context_variables if needed
        args = self._prepare_context_variables(
            prefixed_name, args, context_variables, tool_call_id
        )

        # print shrinked args
        short_args = f"{args}"[:100]
        logger.info(f"Calling tool {prefixed_name} | {short_args}")
        # 1. Collect all available tool names
        all_tools: dict[str, str] = {}  # tool_name -> source ("base" or provider_name)
        for name in self._base_functions.keys():
            all_tools[name] = "base"
        for provider_name, provider in self.providers.items():
            try:
                tools = await provider.list_tools()
                for tool_info in tools:
                    full_name = f"{provider_name}__{tool_info.name}"
                    all_tools[full_name] = provider_name
            except Exception as e:
                logger.warning(
                    f"Failed to list tools from provider '{provider_name}': {e}"
                )

        # 2. Match tool name (exact match first, then suffix match)
        resolved_name = None
        if prefixed_name in all_tools:
            resolved_name = prefixed_name
        else:
            # Suffix matching: find tools ending with the given name
            for name in all_tools.keys():
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

    # ===== Legacy MCP method (deprecated, kept for backward compatibility) =====

    def _convert_functions(
        self, relaxed_schema: bool, allow_transfer: bool
    ) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            desc = parse_func(func, update_by_docstring=False)

            assert isinstance(desc.name, str), "Function name must be a string"

            # Filter transfer functions if not allowed
            if not allow_transfer:
                if desc.name.startswith("transfer_to_") or desc.name.startswith(
                    "call_agent_"
                ):
                    # NOTE: transfer function should start with `transfer_to_`
                    continue

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
        tool_messages = []

        async def _run_single_tool_call(call: dict) -> dict:
            func_name = call["function"]["name"]
            tool_call_id = call["id"]
            start_time = time.time()

            # Try to parse arguments
            try:
                args_str = call["function"]["arguments"]
                if not args_str.endswith("}"):
                    args_str = args_str + "}"
                params = json.loads(call["function"]["arguments"]) or {}
                parse_error = None
            except Exception as e:
                logger.warning(f"Failed to parse arguments for tool '{func_name}': {e}")
                params = {}
                parse_error = e

            allow_timeout = func_name != "call_agent"

            # Pop _background flag before passing params to tool
            run_in_bg = params.pop("_background", False)
            if func_name == "call_agent" and run_in_bg:
                logger.warning(
                    "Ignoring _background=True for call_agent; delegation must remain synchronous"
                )
                run_in_bg = False
            from .background import _bg_output_buffer

            # Handle parse error or execute tool
            if parse_error:
                # Treat as execution failure
                truncated = call["function"]["arguments"][:200]
                if len(call["function"]["arguments"]) > 200:
                    truncated += "..."
                result = (
                    f"Error: Failed to parse tool arguments.\n"
                    f"JSON error: {parse_error}\n"
                    f"Raw arguments: {truncated}"
                )
            elif run_in_bg:
                # Explicit background execution via _background=True
                bg_tool_call_id = f"bg_call_{uuid4()}"
                _stdout_buffer: list[str] = []
                _token = _bg_output_buffer.set(_stdout_buffer)

                try:
                    # Snapshot args before call_tool injects context_variables
                    # (which contains non-serializable functions like _call_agent)
                    bg_args = dict(params)
                    coro = self.call_tool(
                        func_name, params, context_variables,
                        tool_call_id=bg_tool_call_id,
                    )
                    bg_task = self._bg_manager.start(
                        tool_name=func_name,
                        tool_call_id=bg_tool_call_id,
                        args=bg_args,
                        coro=coro,
                        source="explicit",
                    )
                finally:
                    _bg_output_buffer.reset(_token)

                result = {
                    "task_id": bg_task.task_id,
                    "status": "running",
                    "tool_name": func_name,
                    "message": (
                        f"Tool launched in background. Use "
                        f"background_task(action='status', task_id='{bg_task.task_id}') "
                        f"to check progress."
                    ),
                }
            else:
                # Normal execution with timeout adoption
                _stdout_buffer: list[str] = []
                _token = _bg_output_buffer.set(_stdout_buffer)

                call_task = asyncio.create_task(
                    self.call_tool(
                        func_name, params, context_variables, tool_call_id=tool_call_id
                    )
                )

                adopted_to_bg = False
                try:
                    result: Any
                    while True:
                        done, _ = await asyncio.wait(
                            {call_task},
                            timeout=time_delta,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if call_task in done:
                            result = call_task.result()
                            break

                        logger.debug("Check stop when tool calling")
                        elapsed = time.time() - start_time
                        if allow_timeout and timeout is not None and elapsed >= timeout:
                            # Adopt into background instead of cancelling.
                            # Merge _report_output items INTO _stdout_buffer so
                            # post-adoption prints keep accumulating in the same list.
                            report_buf = self._tool_output_buffers.pop(tool_call_id, [])
                            _stdout_buffer.extend(report_buf)
                            bg_task = self._bg_manager.adopt(
                                tool_name=func_name,
                                tool_call_id=tool_call_id,
                                args=params,
                                existing_task=call_task,
                                output_buffer=_stdout_buffer,  # same list object
                            )
                            adopted_to_bg = True
                            result = (
                                f"Tool '{func_name}' exceeded timeout ({timeout}s) and was moved to "
                                f"background execution. task_id='{bg_task.task_id}'. "
                                f"Use background_task(action='status', task_id='{bg_task.task_id}') to check progress and results."
                            )
                            break
                        if check_stop is not None and check_stop(elapsed):
                            call_task.cancel()
                            raise StopRunning()
                    if not adopted_to_bg:
                        self._tool_output_buffers.pop(tool_call_id, None)
                except StopRunning:
                    self._tool_output_buffers.pop(tool_call_id, None)
                    raise
                except SystemExit as e:
                    if not call_task.done():
                        call_task.cancel()
                    result = f"SystemExit: {e}"
                    self._tool_output_buffers.pop(tool_call_id, None)
                except Exception as e:
                    if not call_task.done():
                        call_task.cancel()
                    result = repr(e)
                    self._tool_output_buffers.pop(tool_call_id, None)
                finally:
                    _bg_output_buffer.reset(_token)
                    # Critical Fix: Ensure child task is cancelled if WE are cancelled
                    # But skip if it was adopted to background
                    if not call_task.done() and not self._bg_manager._is_adopted(call_task):
                        logger.warning(f"Cancelling orphaned tool task for {func_name}")
                        call_task.cancel()

            end_timestamp = time.time()
            execution_duration = end_timestamp - start_time

            # P1: Move timestamp fields to _metadata (except top-level timestamp for compatibility)
            tool_message = {
                "role": "tool",
                "tool_name": func_name,
                "name": func_name,
                "id": str(uuid4()),
                "tool_call_id": tool_call_id,
                "timestamp": end_timestamp,  # Keep for backward compatibility
                "_metadata": {
                    "start_timestamp": start_time,
                    "end_timestamp": end_timestamp,
                    "execution_duration": execution_duration,
                },
            }

            if isinstance(result, (Agent, RemoteAgent)):
                tool_message.update(
                    {
                        "transfer": True,
                        "content": result.name,
                    }
                )
            else:
                # Extract and merge nested metadata from tool result if available
                # Tools that use _call_agent internally will return _metadata with cost info
                if isinstance(result, dict) and "_metadata" in result:
                    tool_metadata = result.pop("_metadata", {})
                    # Merge instead of overwrite to preserve all metadata fields
                    tool_message["_metadata"].update(tool_metadata)

                # Process and truncate tool result in one step
                content = process_tool_result(
                    result,
                    max_length=self.max_tool_content_length,
                    tool_name=func_name,
                )
                
                tool_message.update({
                    "raw_content": result,  # Now without _metadata
                    "content": content,
                })


            return tool_message

        tasks = [
            asyncio.create_task(_run_single_tool_call(call)) for call in tool_calls
        ]
        try:
            tool_messages = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            raise

        return tool_messages

    async def _acompletion(
        self,
        messages: list[dict],
        model: str,
        tool_use: bool = True,
        response_format: Any | None = None,
        process_chunk: Callable | None = None,
        allow_transfer: bool = True,
        context_variables: dict | None = None,
    ) -> dict:
        """Get LLM completion for messages (simplified and optimized).

        This method orchestrates the LLM completion workflow:
        1. Process messages for the model
        2. Detect provider and load configuration
        3. Convert functions to tools
        4. Create enhanced chunk processor
        5. Call LLM provider
        6. Add metadata to response

        Args:
            messages: Chat message history
            model: Model identifier (e.g., "gpt-4", "zhipu/glm-4")
            tool_use: Whether to include tools
            response_format: Response format specification
            process_chunk: Optional callback for streaming chunks
            allow_transfer: Whether to allow agent transfers

        Returns:
            Message dictionary with id, timestamps, and generation_duration
        """
        # Initialize timing tracker
        tracker = TimingTracker()
        tracker.start("total")
        request_start_time = time.time()

        logger.info(f"🚀 [Agent:{self.name}] Starting LLM request for model: {model}")

        # Step 1: Process messages for the model
        async with tracker.measure("message_processing"):
            from pantheon.utils.token_optimization import (
                _estimate_message_tokens,
                build_llm_view_async,
                inject_cache_control_markers,
                supports_explicit_cache_control,
            )

            run_context = get_current_run_context()
            if run_context is not None:
                run_context.current_model = model
            optimization_memory = run_context.memory if run_context else None
            is_main_thread = (
                run_context.execution_context_id is None if run_context else True
            )
            messages = await build_llm_view_async(
                messages,
                memory=optimization_memory,
                is_main_thread=is_main_thread,
                autocompact_model=model,
                context_window_model=model,
            )
            logger.info(
                "[resume] prompt_view agent={} model={} messages={} est_tokens={}",
                self.name,
                model,
                len(messages),
                sum(_estimate_message_tokens(message) for message in messages),
            )
            messages = process_messages_for_model(messages, model)
            # Token optimization can drop earlier assistant tool-call messages
            # while leaving later tool results. Re-sanitize right before the
            # provider call so Responses API inputs never contain orphaned
            # function_call_output items.
            messages = self._sanitize_messages(messages)
            # Inject prompt-cache markers for providers that support
            # explicit cache_control (Anthropic, Qwen).
            # OpenAI/DeepSeek/Gemini use automatic prefix caching —
            # stabilize_tool_definitions() ensures stable prefixes for them.
            if supports_explicit_cache_control(model):
                messages = inject_cache_control_markers(messages)
            if run_context is not None:
                # Selective copy: shallow for messages with string content,
                # deepcopy only for messages with list content (Anthropic blocks
                # from inject_cache_control_markers) to avoid mutation issues.
                cached = []
                for m in messages:
                    if isinstance(m.get("content"), list):
                        cached.append(copy.deepcopy(m))
                    else:
                        cached.append({**m})
                run_context.cache_safe_prompt_messages = cached

        # Step 2: Detect provider and get configuration
        provider_config = detect_provider(model, self.relaxed_schema)

        # Step 3: Get base URL and API key from environment if available
        # Skip if detect_provider already set them (e.g. OpenAI-compatible providers)
        using_universal_base = False
        if not provider_config.base_url:
            base_url = get_base_url(provider_config.provider_type)
            if base_url:
                provider_config.base_url = base_url
                # Check if base_url came from LLM_API_BASE (universal fallback)
                from pantheon.settings import get_settings
                provider_env = f"{provider_config.provider_type.value.upper()}_API_BASE"
                if not get_settings().get_api_key(provider_env):
                    using_universal_base = True
        if not provider_config.api_key:
            if using_universal_base:
                # Universal proxy: prefer LLM_API_KEY over provider-specific key
                from pantheon.settings import get_settings
                api_key = get_settings().get_api_key("LLM_API_KEY")
                if not api_key:
                    api_key = get_api_key_for_provider(provider_config.provider_type)
            else:
                api_key = get_api_key_for_provider(provider_config.provider_type)
            if api_key:
                provider_config.api_key = api_key

        # Step 4: Get unified tools (base functions + provider tools)
        tools = None
        if tool_use:
            async with tracker.measure("tools_conversion"):
                # Use get_tools_for_llm() for unified tool access
                # This includes both base_functions and provider tools
                tools = await self.get_tools_for_llm() or None
                if run_context is not None and tools is not None:
                    run_context.cache_safe_tool_definitions = copy.deepcopy(tools)

                # For non-OpenAI providers or OpenAI-compatible providers, adjust tool format
                # OpenAI-compatible providers (e.g. minimax) have api_key set in config
                is_compat_provider = provider_config.api_key is not None
                if (provider_config.provider_type.value != "openai" or is_compat_provider) and tools:
                    for tool in tools:
                        if "function" in tool:
                            func = tool["function"]
                            # Remove strict mode (not supported by non-OpenAI APIs)
                            func.pop("strict", None)
                            # Ensure description is non-empty
                            if not func.get("description"):
                                func["description"] = func.get("name", "tool")
                            # Ensure parameters is present (required by some providers e.g. MiniMax)
                            if "parameters" not in func:
                                func["parameters"] = {
                                    "type": "object",
                                    "properties": {},
                                    "required": [],
                                }
                            else:
                                # Remove unsupported additionalProperties
                                func["parameters"].pop(
                                    "additionalProperties", None
                                )

        # Step 5: Create message ID and enhanced chunk processor
        message_id = str(uuid4())
        enhanced_process_chunk = create_enhanced_process_chunk(
            process_chunk, message_id, agent_name=self.name
        )

        # Step 6: Send begin chunk
        if enhanced_process_chunk:
            async with tracker.measure("begin_chunk"):
                await enhanced_process_chunk({"begin": True})

        # Step 7: Merge model_params from context_variables if provided
        model_params = self.model_params
        if context_variables and "model_params" in context_variables:
            # Runtime overrides instance defaults
            model_params = {**self.model_params, **context_variables["model_params"]}

        if run_context is not None:
            from pantheon.utils.token_optimization import build_cache_safe_runtime_params

            run_context.cache_safe_runtime_params = build_cache_safe_runtime_params(
                model=model,
                model_params=model_params,
                response_format=response_format,
            )

        # Step 8: Call LLM provider (unified interface)
        # logger.info(f"Raw messages: {messages}")

        async with tracker.measure("llm_api"):
            message = await call_llm_provider(
                config=provider_config,
                messages=messages,
                tools=tools,
                response_format=response_format,
                process_chunk=enhanced_process_chunk,
                model_params=model_params,
            )

        if message is None:
            message = {"role": "assistant", "content": "Error: Empty response from model."}

        # Step 8: Add metadata to message
        end_timestamp = time.time()
        total_time = tracker.end("total")

        message["id"] = message_id
        message["timestamp"] = end_timestamp  # Keep backward compatibility
        
        message.setdefault("_metadata", {}).update({
            "start_timestamp": request_start_time,
            "end_timestamp": end_timestamp,
        })

        # Step 9: Collect stats and log timing
        # ✅ Use lightweight collection for Write mode (O(1))
        from pantheon.utils.llm import collect_message_stats_lightweight
        
        collect_message_stats_lightweight(
            message=message,
            messages=messages,
            model=model,
        )
        
        # ✅ Simplified logging using only required metadata fields
        timings = tracker.get_all()
        meta = message.get("_metadata", {})
        
        # Extract required fields
        total_tokens = meta.get("total_tokens", 0)
        max_tokens = meta.get("max_tokens", 200000)
        current_cost = meta.get("current_cost", 0)

        # Calculate usage percentage
        usage_pct = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0

        # Determine if this is a free OAuth channel (shadow cost)
        _oauth_prefixes = ("codex/", "gemini-cli/")
        _is_shadow = isinstance(model, str) and any(model.startswith(p) for p in _oauth_prefixes)
        _cost_label = f"~${current_cost:.4f} (shadow)" if _is_shadow else f"${current_cost:.4f}"

        # Format log message
        timing_log = (
            f"📊 [Agent:{self.name}] "
            f"⏳ Timing: Total: {total_time:.3f}s | "
            f"Message: {timings.get('message_processing', 0):.3f}s | "
            f"Begin: {timings.get('begin_chunk', 0):.3f}s | "
            f"LLM: {timings['llm_api']:.3f}s | "
            f"Tool: {timings.get('tools_conversion', 0):.3f}s for {len(tools or [])} tools "
            f"💬 Tokens: {total_tokens:,} | "
            f"Usage: {usage_pct:.1f}% | "
            f"Cost: {_cost_label}"
        )
        
        # Add warning if usage is high
        if usage_pct >= 95:
            timing_log += "\n⚠️  WARNING: Context usage ≥95%"
        elif usage_pct >= 90:
            timing_log += "\n⚠️  Warning: Context usage ≥90%"
        
        logger.info(timing_log)

        return message

    async def _acompletion_with_models(
        self,
        history,
        tool_use,
        response_format,
        process_chunk,
        allow_transfer,
        model: str | list[str] | None = None,
        context_variables: dict | None = None,
    ):
        """Try multiple models with fallback and exponential backoff retry.

        For each model, transient errors (overloaded, rate-limit, 5xx) are
        retried with exponential backoff.  Non-transient errors skip directly
        to the next model.  The adapter's ``num_retries`` still handles initial
        connection-level retries; this layer covers mid-stream failures that
        the adapter cannot retry on its own.
        """
        # --- Read retry settings (with sensible defaults) ---
        from .settings import get_settings
        retry_cfg = get_settings().get("llm_retry", {})
        if not isinstance(retry_cfg, dict):
            retry_cfg = {}
        max_retries: int = int(retry_cfg.get("max_retries", 3))
        base_delay: float = float(retry_cfg.get("base_delay", 1.0))
        max_delay: float = float(retry_cfg.get("max_delay", 30.0))
        jitter: float = float(retry_cfg.get("jitter", 0.5))

        # --- Prepare model list ---
        if model is None:
            models = self.models
        else:
            if isinstance(model, str):
                models = [model] + self.models
            else:
                models = model + self.models

        if not models:
            raise RuntimeError(f"No model is available. models: {models}")

        model_error_count = 0
        last_error = None

        for model_name in models:
            if model_error_count > 0:
                logger.warning(
                    f"Trying {model_name} due to previous model failure"
                )

            # max_retries *additional* attempts after the first try
            for attempt in range(max_retries + 1):
                try:
                    message = await self._acompletion(
                        history,
                        model=model_name,
                        tool_use=tool_use,
                        response_format=response_format,
                        process_chunk=process_chunk,
                        allow_transfer=allow_transfer,
                        context_variables=context_variables,
                    )
                    return message

                except StopRunning:
                    raise
                except Exception as e:
                    last_error = e
                    import traceback
                    logger.error(f"[Agent:{self.name}] Full traceback:\n{traceback.format_exc()}")

                    if _is_retryable_error(e) and attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        delay *= random.uniform(1 - jitter, 1 + jitter)
                        delay = max(delay, 0)
                        logger.warning(
                            f"[Agent:{self.name}] Transient error on {model_name} "
                            f"(attempt {attempt + 1}/{max_retries + 1}): "
                            f"{type(e).__name__}: {e}. Retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Non-retryable or retries exhausted — log and move to next model
                    error_type = type(e).__name__
                    if "RateLimitError" in error_type or "rate" in str(e).lower():
                        logger.error(
                            f"Rate limit exceeded for {model_name} after retries: {e}"
                        )
                    else:
                        logger.error(f"Error completing with model {model_name}: {e}")
                    model_error_count += 1
                    break  # break retry loop, continue to next model

        # All models failed
        raise RuntimeError(
            f"All {len(models)} model(s) failed after {model_error_count} attempts. "
            f"Models tried: {models}. Last error: {last_error}"
        )

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
        allow_transfer: bool = True,
        execution_context_id: str
        | None = None,  # NEW: Context ID for message filtering
    ) -> ResponseDetails | AgentTransfer:
        response_format = response_format or self.response_format
        history = copy.deepcopy(messages)

        # Use _llm_content for LLM if present (assembled user messages)
        for msg in history:
            if "_llm_content" in msg:
                msg["content"] = msg["_llm_content"]

        # Expand file:// image references to Base64 for LLM API call
        from .utils.vision import expand_image_references_for_llm

        history = expand_image_references_for_llm(history)

        tool_timeout = tool_timeout or self.tool_timeout

        # Use instructions directly - all prompt composition happens at template parsing time
        # Render system prompt: substitutes ${{}} variables and appends standard context blocks
        system_prompt = self._render_system_prompt(
            self.instructions, context_variables or {}
        )

        current_timestamp = time.time()

        if (len(history) > 0) and (history[0]["role"] == "system"):
            history[0]["content"] = system_prompt
            history[0]["timestamp"] = current_timestamp
            if "id" not in history[0]:
                history[0]["id"] = str(uuid4())
            # Mark system message with execution_context_id (not done in _process_step_message)
            if execution_context_id is not None:
                history[0]["execution_context_id"] = execution_context_id
        else:
            system_msg = {
                "role": "system",
                "content": system_prompt,
                "timestamp": current_timestamp,
                "id": str(uuid4()),
            }
            # Mark system message with execution_context_id (not done in _process_step_message)
            if execution_context_id is not None:
                system_msg["execution_context_id"] = execution_context_id
            history.insert(0, system_msg)
        init_len = len(history)
        context_variables = context_variables or {}

        if response_format:
            Response = create_model("Response", result=(response_format, ...))
        else:
            Response = None

        async def _process_chunk(chunk: dict):
            if process_chunk is not None:
                await run_func(process_chunk, chunk)
            if check_stop is not None and check_stop(chunk):
                raise StopRunning()

        while len(history) - init_len < max_turns:
            # Build history for LLM with ephemeral messages from hooks (not persisted)
            history_for_llm = list(history)
            for hook in self._ephemeral_hooks:
                plugin_msgs = await hook(history, context_variables or {})
                history_for_llm.extend(plugin_msgs)

            # Inject background task completion notifications (ephemeral)
            bg_notifs = self._bg_manager.drain_notifications()
            if bg_notifs:
                lines = []
                for bg_task in bg_notifs:
                    summary = self._bg_manager.to_summary(bg_task)
                    result_preview = str(summary.get("result", ""))[:500]
                    lines.append(
                        f"- task_id='{bg_task.task_id}', tool='{bg_task.tool_name}', "
                        f"status='{bg_task.status}', result: {result_preview}"
                    )
                notif_msg = {
                    "role": "user",
                    "content": (
                        "<EPHEMERAL_MESSAGE>\n"
                        "[Background Task Notification] The following background tasks have completed:\n"
                        + "\n".join(lines)
                        + "\nPlease inform the user about these results."
                        + "\n</EPHEMERAL_MESSAGE>"
                    ),
                }
                history_for_llm = list(history_for_llm) + [notif_msg]

            message = await self._acompletion_with_models(
                history_for_llm,
                tool_use,
                Response,
                _process_chunk,
                allow_transfer,
                model=model,
                context_variables=context_variables,
            )

            if Response is not None:
                content = message.get("content")
                if content:
                    parsed = Response.model_validate_json(content)
                    message["parsed"] = parsed.result

            message["agent_name"] = self.name

            history.append(message)
            self.events_queue.put_nowait(message)
            if process_step_message:
                await run_func(process_step_message, message)

            # If no tool calls, stop conversation
            # BUT: If we have reasoning content WITHOUT actual content, continue to let the model output its conclusion/tool calls
            # This handles models that output reasoning in one turn and content/tools in the next
            # However, if the model outputs both reasoning AND content in the same turn (e.g., Gemini),
            # we should stop to avoid an unnecessary extra loop that returns empty content
            has_reasoning = message.get("reasoning_content") and str(message.get("reasoning_content")).strip()
            has_content = message.get("content") and str(message.get("content")).strip()
            
            # Stop if:
            # 1. No tool calls AND no reasoning (normal completion)
            # 2. No tool calls AND has reasoning BUT also has content (reasoning + content in same turn)
            # Continue only if: has reasoning but NO content (waiting for model to output conclusion)
            if not message.get("tool_calls"):
                if not has_reasoning or has_content:
                    break

            tool_messages = await self._handle_tool_calls(
                message.get("tool_calls") or [],
                context_variables=context_variables,
                timeout=tool_timeout,
                check_stop=check_stop,
            )

            # Process tool calls for state tracking via hooks
            for hook in self._tool_tracking_hooks:
                await hook(
                    message.get("tool_calls") or [],
                    tool_messages,
                    context_variables or {},
                )

            # Filter out all transfer messages - they will be handled in _prepare_execution_context()
            # Both call_agent and transfer_to_* transfers should skip history addition here
            non_transfer_messages = []
            transfer_message = None
            interrupt_message = None

            for msg in tool_messages:
                if msg.get("transfer"):
                    # Skip all transfer messages
                    transfer_message = msg
                else:
                    # Check for notify_user interrupt
                    raw_content = msg.get("raw_content", {})
                    if isinstance(raw_content, dict) and raw_content.get("interrupt"):
                        interrupt_message = msg
                    # Keep regular tool messages
                    non_transfer_messages.append(msg)

            history.extend(non_transfer_messages)
            for msg in non_transfer_messages:
                self.events_queue.put_nowait(msg)

            if process_step_message:
                for msg in process_messages_for_hook_func(non_transfer_messages):
                    msg["agent_name"] = self.name  # Add agent_name to tool messages
                    await run_func(process_step_message, msg)

            if transfer_message:
                # Add the transfer tool result to history so the next agent sees it
                # This ensures every tool_call has a corresponding tool response
                # Without this, OpenAI API will reject: "tool_calls must be followed by tool messages"
                transfer_tool_result = {
                    "role": "tool",
                    "tool_call_id": transfer_message.get("tool_call_id"),
                    "content": f"Transferred to {transfer_message['content']}",
                }
                history.append(transfer_tool_result)

                transfer = AgentTransfer(
                    from_agent=self.name,
                    to_agent=transfer_message["content"],
                    history=history,
                    context_variables=context_variables,
                    init_message_length=init_len,
                    tool_call_id=transfer_message.get("tool_call_id"),
                )
                return transfer

            # Handle notify_user interrupt - break loop to return control to user
            if interrupt_message:
                break

        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
            execution_context_id=execution_context_id,  # NEW: Pass context ID through response
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

        Note: AgentTransfer is NOT handled here - it's processed directly in
        _prepare_execution_context() before calling this method.

        Args:
            msg: The input message (user input types, not AgentTransfer)

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

    async def _collect_injections(self, input_text: str, injector_context: dict) -> list[dict]:
        """Collect injections from all registered context injectors."""
        pending_injections = []
        for injector in self.context_injectors:
            try:
                injected_content = await injector.inject(input_text, injector_context)
                if injected_content:
                    pending_injections.append({
                        "content": injected_content,
                        "source": type(injector).__name__
                    })
            except Exception as e:
                logger.error(f"Context injection failed: {e}", exc_info=True)
        return pending_injections

    async def _inject_context_to_messages(
        self,
        messages: list[dict],
        context_variables: dict,
    ) -> None:
        """
        Inject dynamic context into user messages via context injectors.
        
        Modifies messages in-place by appending injected content to _llm_content field.
        Only injects into user messages (role="user").
        """
        if not self.context_injectors:
            return

        # Create _call_agent callback definition
        async def _call_agent_wrap(
            messages: list,
            system_prompt: str | None = None,
            model: str | None = None,
            use_memory: bool = False,
        ) -> dict:
            memory = self.memory[:-1] if use_memory else None  # Exclude current message
            preferred_model = get_current_run_model() or (self.models[0] if self.models else None)
            return await _call_agent(
                messages=messages,
                system_prompt=system_prompt,
                model=_resolve_model_spec_with_current_provider(
                    model,
                    current_model=preferred_model,
                ),
                memory=memory,
            )

        inherited_model = get_current_run_model()
        caller_models = list(self.models)
        if inherited_model:
            caller_models = [inherited_model, *[
                candidate for candidate in caller_models
                if candidate != inherited_model
            ]]
        
        # Build context for injectors
        injector_context = {
            "agent_name": self.name,
            "_call_agent": _call_agent_wrap,
            "caller_models": caller_models,
            **context_variables,
        }
        
        for message in messages:
            # Only inject into user messages
            if message.get("role") != "user":
                continue
            
            input_text = _get_message_text(message)
            if not input_text:
                continue

            injections = await self._collect_injections(input_text, injector_context)
            if injections:
                _apply_injections(message, injections)

    async def _prepare_execution_context(
        self,
        msg: AgentInput,
        execution_context_id: str | None = None,
        context_variables: dict | None = None,
        memory: "Memory | None" = None,
        use_memory: bool | None = None,
    ) -> ExecutionContext:
        """Prepare execution context based on input type.

        Handles two paths:
        1. AgentTransfer (delegated from another agent): Uses transfer's history and context
        2. Normal user input: Converts input to messages and optionally merges with memory
        """
        # Determine whether to use memory
        should_use_memory = use_memory if use_memory is not None else self.use_memory
        memory_instance = memory or self.memory
        working_context_variables = (context_variables or {}).copy()
        fork_context_messages = working_context_variables.pop(
            "_cache_safe_fork_context_messages",
            None,
        )

        input_messages = None  # Only set for normal user input, not AgentTransfer

        if isinstance(msg, AgentTransfer):
            # Delegation path: use transfer's history and context
            conversation_history = self._filter_messages_by_execution_context(
                msg.history, execution_context_id
            )
            conversation_history = self._sanitize_messages(conversation_history)
            context_variables = msg.context_variables
        else:
            # User input path: convert input to messages
            input_messages = await self._input_to_openai_messages(msg)

            # Process images: convert Base64 to file:// paths for efficient storage
            from .utils.vision import get_image_store

            image_store = get_image_store()
            chat_id = memory_instance.id if memory_instance else "default"
            for m in input_messages:
                image_store.process_message_images(m, chat_id)

            logger.debug(
                f"Input messages: {input_messages} , memory_length: {len(memory_instance.get_messages(execution_context_id=execution_context_id, for_llm=False))} "
                f"raw memory_length: {len(memory_instance.get_messages(for_llm=False))} memory_id: {memory_instance.id}"
            )
            conversation_history = []
            if should_use_memory and memory_instance:
                from pantheon.repl.conversationRecovery import loadConversationForResume
                from pantheon.repl.sessionRestore import processResumedConversation

                raw_history = memory_instance.get_messages(
                    execution_context_id=execution_context_id,
                    for_llm=False,
                )
                resume_result = loadConversationForResume(
                    memory_instance,
                    execution_context_id=execution_context_id,
                )
                if resume_result is not None:
                    processed_resume = await processResumedConversation(
                        resume_result,
                        {"forkSession": False},
                        {"memory": memory_instance, "initialState": {}},
                    )
                    conversation_history = processed_resume.get("messages", [])
                    logger.info(
                        "[resume] agent={} execution_context_id={} raw_history={} resumed_history={}",
                        self.name,
                        execution_context_id,
                        len(raw_history),
                        len(conversation_history),
                    )
                else:
                    conversation_history = raw_history
            if isinstance(fork_context_messages, list) and fork_context_messages:
                conversation_history = [
                    *copy.deepcopy(fork_context_messages),
                    *conversation_history,
                ]
            conversation_history += input_messages
            conversation_history = self._sanitize_messages(conversation_history)

        # preserve execution_context_id if tool need
        context_variables = working_context_variables

        # Inject global context variables from settings
        from .settings import get_settings

        context_variables.update(get_settings().get_context_variables())

        if execution_context_id is not None:
            context_variables["execution_context_id"] = execution_context_id
        # preserve client_id from context_variables or memory
        context_variables[_CLIENT_ID_NAME] = context_variables.get(
            _CLIENT_ID_NAME,
            memory_instance.id,
        )

        # Apply context injectors to user's NEW input messages only
        # (not the entire conversation_history to avoid re-injecting into memory)
        if self.context_injectors and input_messages:
            await self._inject_context_to_messages(
                input_messages,
                context_variables,
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
        allow_transfer: bool = True,
        execution_context_id: str | None = None,
    ) -> AgentResponse | AgentTransfer:
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
            allow_transfer: Whether to allow transfer to another agent.
            execution_context_id: Unique identifier for sub-agent delegation contexts.
                Used for message filtering in _run_stream to isolate messages by
                delegation context. None for primary/team agents.

        Returns:
            The agent response. Either an AgentResponse or an AgentTransfer.
            If the agent is interrupted, the AgentResponse will have the interrupt flag set to True.
            If the agent is transferring to another agent, the AgentTransfer will be returned.
        """
        # Prepare execution context based on input type
        # This handles both AgentTransfer delegation and normal user input paths
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
        # all messages: (user input, sub agent mock user input, sub agent transfer tool response)
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
                allow_transfer=allow_transfer,
                execution_context_id=exec_context.execution_context_id,
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

        # Handle response - could be AgentTransfer or ResponseDetails
        if isinstance(run_result, AgentTransfer):
            return run_result
        else:
            # Extract content from the last message
            last_message = run_result.messages[-1]
            if response_format_to_use:
                content = last_message.get("parsed")
            else:
                content = last_message.get("content")
            return AgentResponse(
                agent_name=self.name,
                content=content,
                details=run_result,
            )

    # ===== Event-Driven Loop =====

    async def run_loop(
        self,
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        check_stop: Callable | None = None,
        context_variables: dict | None = None,
        memory: "Memory | None" = None,
        tool_timeout: int | None = None,
        model: str | list[str] | None = None,
        on_response: Callable | None = None,
        on_error: Callable | None = None,
    ) -> None:
        """Persistent event-driven loop consuming messages from input_queue.

        Blocks until stop_loop() is called or the task is cancelled.
        Each message dequeued triggers a full agent.run() cycle.
        Background task completions auto-enqueue notifications, so the
        agent is automatically triggered when bg tasks finish.

        Args:
            process_chunk: Streaming chunk callback (forwarded to run()).
            process_step_message: Step message callback (forwarded to run()).
            check_stop: Stop check callback (forwarded to run()).
            context_variables: Shared context variables for all runs.
            memory: Memory instance to use.
            tool_timeout: Tool timeout (forwarded to run()).
            model: Model override (forwarded to run()).
            on_response: Callback(AgentResponse) after each successful run.
            on_error: Callback(Exception) when a run fails.
        """
        self._loop_running = True
        self._bg_manager.on_complete = self._on_bg_task_complete_for_loop

        try:
            while self._loop_running:
                msg = await self.input_queue.get()
                if msg is None:  # Sentinel from stop_loop()
                    self.input_queue.task_done()
                    break
                try:
                    response = await self.run(
                        msg=msg,
                        process_chunk=process_chunk,
                        process_step_message=process_step_message,
                        check_stop=check_stop,
                        context_variables=context_variables,
                        memory=memory,
                        tool_timeout=tool_timeout,
                        model=model,
                    )
                    if on_response:
                        await run_func(on_response, response)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"run_loop error: {e}")
                    if on_error:
                        try:
                            await run_func(on_error, e)
                        except Exception:
                            pass
                finally:
                    self.input_queue.task_done()
        finally:
            self._loop_running = False
            self._bg_manager.on_complete = None

    def stop_loop(self):
        """Signal run_loop() to exit after current iteration."""
        self._loop_running = False
        self.input_queue.put_nowait(None)  # Sentinel to unblock .get()

    def _on_bg_task_complete_for_loop(self, bg_task):
        """Push lightweight trigger to input_queue on bg task completion.

        The real task data comes from drain_notifications() in _run_stream
        (ephemeral message injection, already implemented).
        """
        try:
            self.input_queue.put_nowait(
                f"[Background task '{bg_task.task_id}' ({bg_task.tool_name}) "
                f"completed with status: {bg_task.status}]"
            )
        except Exception:
            pass

    def setup_bg_notify_queue(self, queue: asyncio.Queue):
        """Wire bg task completion to an external asyncio.Queue.

        Alternative to run_loop() for frontends that manage their own
        event loops (e.g. REPL with prompt_toolkit, ChatRoom server).

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

    async def serve(self, **kwargs):
        """Serve the agent to a remote server."""

        service = AgentService(self, **kwargs)
        return await service.run()


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

    current_run_model = get_current_run_model()
    inherited_model = _resolve_model_spec_with_current_provider(
        model or current_run_model,
        current_model=current_run_model,
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
