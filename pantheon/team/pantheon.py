import copy
import re
import uuid
from typing import TYPE_CHECKING, List, Optional

from pantheon.agent import (
    Agent,
    AgentInput,
    AgentTransfer,
    RemoteAgent,
    get_current_run_context,
)
from pantheon.internal.memory import Memory
from pantheon.utils.log import logger
from pantheon.utils.misc import run_func
from .base import Team

if TYPE_CHECKING:
    from pantheon.team.plugin import TeamPlugin


LIST_AGENTS_DOC = """List all available agents and their capabilities.

Returns a list of agent names and descriptions. Use this to:
- Discover agents you can delegate to via call_agent()
- Choose the right agent for specific task types and then
  call_agent() to delegate the task.

Call this before delegating if unsure which agent handles a task."""

CALL_AGENT_DOC = """Delegate a task to another agent in the team.

Args:
    agent_name: Name of the target agent (use list_agents() to discover).
    instruction: Task description with the following structure:
        - **Goal**: What needs to be accomplished and why it matters.
        - **Context**: All background the agent needs (files, data, constraints).
          Assume the agent has no memory of prior conversation.
        - **Expected Outcome**: Format, files, or deliverables expected.

Returns:
    Response content from the target agent.

Example instruction:
    'Goal: Analyze gene expression patterns in the PBMC dataset.\\n\\n'
    'Context: Dataset at /data/pbmc.h5ad, already preprocessed. '
    'Focus on T cell subpopulations.\\n\\n'
    'Expected Outcome: Report with UMAP visualization and marker genes.'"""


def _get_cache_safe_child_run_overrides(
    run_context,
    target_agent: Agent | RemoteAgent,
    child_context_variables: dict,
) -> tuple[dict, dict]:
    cache_params = getattr(run_context, "cache_safe_runtime_params", None)
    caller_agent = getattr(run_context, "agent", None)

    if (
        cache_params is None
        or not isinstance(caller_agent, Agent)
        or not isinstance(target_agent, Agent)
    ):
        return {}, child_context_variables

    from pantheon.utils.token_optimization import normalize_cache_safe_value

    if list(target_agent.models) != list(caller_agent.models):
        return {}, child_context_variables

    if normalize_cache_safe_value(target_agent.response_format) != cache_params.response_format_normalized:
        return {}, child_context_variables

    overrides = {
        "model": cache_params.model,
        "response_format": cache_params.response_format_raw,
    }

    updated_context_variables = dict(child_context_variables)
    if (
        "model_params" not in updated_context_variables
        and cache_params.model_params_raw
    ):
        updated_context_variables["model_params"] = copy.deepcopy(
            cache_params.model_params_raw
        )

    return overrides, updated_context_variables


def _build_structured_fork_context(run_context) -> "list[dict] | None":
    """Build a structured fork context from the parent's optimised history.

    Mirrors Claude Code's forkContextMessages: the child receives the parent's
    already-budget+snipped message list (sans system message) as its initial
    context, rather than a plain-text summary.  This preserves tool-call
    structure and lets the child reason over the actual conversation, not a
    lossy narration of it.

    Returns None if there is no history worth forwarding.
    """
    memory = getattr(run_context, "memory", None)
    if memory is None:
        return None

    # Use the already-computed cache_safe_prompt_messages if available —
    # those have already been through build_llm_view (budget + microcompact).
    cached = getattr(run_context, "cache_safe_prompt_messages", None)
    if cached:
        result = [
            copy.deepcopy(m)
            for m in cached
            if m.get("role") != "system"
        ]
        return result or None

    # Fallback: build fresh view from memory
    from pantheon.utils.token_optimization import build_llm_view

    raw = memory.get_messages(None)
    if not raw:
        return None
    projected = build_llm_view(raw, memory=memory, is_main_thread=True)
    result = [m for m in projected if m.get("role") != "system"]
    return result or None


async def _get_cache_safe_child_fork_context_messages(
    run_context,
    target_agent: Agent | RemoteAgent,
) -> list[dict] | None:
    caller_agent = getattr(run_context, "agent", None)
    parent_messages = getattr(run_context, "cache_safe_prompt_messages", None)
    parent_tools = getattr(run_context, "cache_safe_tool_definitions", None)

    if (
        parent_messages is None
        or parent_tools is None
        or not isinstance(caller_agent, Agent)
        or not isinstance(target_agent, Agent)
    ):
        return None

    if target_agent.instructions != caller_agent.instructions:
        return None

    if list(target_agent.models) != list(caller_agent.models):
        return None

    from pantheon.utils.token_optimization import normalize_cache_safe_value

    target_tools = await target_agent.get_tools_for_llm()
    if normalize_cache_safe_value(target_tools) != normalize_cache_safe_value(parent_tools):
        return None

    fork_context_messages = [
        copy.deepcopy(message)
        for message in parent_messages
        if message.get("role") != "system"
    ]
    return fork_context_messages or None



def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "agent"


def _build_execution_context_id(
    context_variables: dict,
    run_context,
    depth: int,
    target_name: str,
) -> str:
    """Return ID formatted as '<root>|d<depth>|<agent>|<rand4>'.

    - root: derived from parent's execution_context_id or caller memory id
    - depth: current delegation depth
    - agent: safe version of the child agent name
    - rand4: short random suffix to keep IDs unique even for parallel calls
    """

    def _extract_root_token() -> str:
        # P2: Read from context_variables top level (messages have it there)
        execution_context_id = context_variables.get("execution_context_id")
        if execution_context_id and "|" in execution_context_id:
            return execution_context_id.split("|", 1)[0]

        memory = getattr(run_context, "memory", None)
        memory_id = getattr(memory, "id", "memory")
        mem_prefix = str(memory_id)[:8] if memory_id else "memory"
        return f"{mem_prefix}-{uuid.uuid4().hex[:6]}"

    root_token = _extract_root_token()

    return "|".join(
        [
            root_token,
            f"d{depth}",
            _slugify(target_name),
            uuid.uuid4().hex[:4],
        ]
    )


def _detect_and_reset_stale_chain(
    enhanced_chain: list,
    agent_names: list,
    caller_name: str,
    target_name: str,
) -> tuple[list, list]:
    """Detect stale chain_path and auto-reset instead of throwing error.
    
    When the system detects that:
    1. Target agent already appears in chain_path (would trigger loop error)
    2. Caller is the ROOT of existing chain (first element without call_id)
    
    This indicates a NEW delegation from root, but chain_path contains stale
    data from a previous turn. Solution: reset to fresh chain.
    
    Args:
        enhanced_chain: Current chain_path list
        agent_names: Extracted agent names from chain
        caller_name: Name of the calling agent
        target_name: Name of the target agent
        
    Returns:
        Tuple of (enhanced_chain, agent_names) - possibly reset if stale detected
        
    Raises:
        RuntimeError: If a real delegation loop is detected (e.g., A -> B -> A)
    """
    if target_name not in agent_names:
        # No conflict, return as-is
        return enhanced_chain, agent_names
    
    # Target already in chain - check if stale or real loop
    is_caller_root = (
        len(enhanced_chain) > 0 and 
        enhanced_chain[0] == caller_name and  # Caller is first element
        ":" not in enhanced_chain[0]  # Without call_id (root format)
    )
    
    if is_caller_root:
        # Stale chain_path from previous turn - reset
        logger.warning(
            f"Detected stale chain_path (caller {caller_name} is root but "
            f"target {target_name} already in chain). Resetting chain. "
            f"Original chain: {enhanced_chain}"
        )
        return [caller_name], [caller_name]
    else:
        # Real loop (e.g., A -> B -> A)
        raise RuntimeError(
            "Delegation loop detected: this agent already appears in the current chain."
        )


def _build_child_context_metadata(
    run_context,
    target_agent: Agent,
    context_variables: dict,
    max_depth: int = 5,
) -> dict:
    if run_context is None:
        raise RuntimeError(
            "call_agent must be executed within an active agent tool call"
        )
    parent_metadata = context_variables.get("_metadata") or {}
    parent_path = parent_metadata.get("chain_path")
    
    # P2: Build enhanced chain_path with agent:call_id format
    enhanced_chain = []
    if parent_path:
        # Inherit parent's enhanced path
        enhanced_chain = list(parent_path)
    else:
        # Root agent (no call_id)
        enhanced_chain = [run_context.agent.name]
    
    # Extract agent names for loop detection
    agent_names = []
    for entry in enhanced_chain:
        agent_name = entry.split(":", 1)[0] if ":" in entry else entry
        agent_names.append(agent_name)
    
    # Detect and handle stale chain_path or real loop
    enhanced_chain, agent_names = _detect_and_reset_stale_chain(
        enhanced_chain=enhanced_chain,
        agent_names=agent_names,
        caller_name=run_context.agent.name,
        target_name=target_agent.name,
    )
    
    child_depth = max(len(enhanced_chain), 0)  # Depth is length of existing chain
    if child_depth > max_depth:
        raise RuntimeError("Delegation depth limit reached.")

    execution_context_id = _build_execution_context_id(
        context_variables, run_context, child_depth, target_agent.name
    )

    tool_call_id = context_variables.get("tool_call_id") or (
        "call_" + uuid.uuid4().hex[:12]
    )
    
    # P2: Add current agent with call_id to chain
    enhanced_chain.append(f"{target_agent.name}:{tool_call_id}")

    # P2: Simplified metadata - only chain_path
    metadata = {
        "chain_path": enhanced_chain,
    }

    # P2: Return execution_context_id separately (will be set at message top level)
    return metadata, execution_context_id


class PantheonTeam(Team):
    """Pantheon team structure with unified agent architecture.

    All agents are treated equally with the same capabilities:
    - list_agents(): Discover other agents in the team
    - call_agent(): Delegate tasks to other agents
    - transfer_to_agent(): Hand off control to another agent

    Features enabled when team has multiple agents (len > 1).
    """

    def __init__(
        self,
        agents: list[Agent | RemoteAgent],
        use_summary: bool = True,
        max_delegate_depth: int | None = 5,
        allow_transfer: bool = False,
        plugins: Optional[List["TeamPlugin"]] = None,
    ):
        """Initialize PantheonTeam with unified agent architecture.

        Args:
            agents: List of agents in the team.
            use_summary: If True (default), generate summary + recent context
                         instead of full history for delegation. Set to False
                         to pass only the raw instruction.
            max_delegate_depth: Maximum depth for nested call_agent calls.
            allow_transfer: If True, add transfer_to_agent tool to agents.
            plugins: Optional list of TeamPlugin instances for extending functionality.

        Note:
            All agents are equal - the first one is used as the default
            entry point but receives no special treatment.

            Plugins handle optional features like learning, compression, monitoring, etc.
        """
        if not agents:
            raise ValueError("Team must have at least one agent")

        self.team_agents = agents
        self.use_summary = use_summary
        self.max_delegate_depth = max_delegate_depth
        self.allow_transfer = allow_transfer

        # Plugin system for extending team functionality
        self.plugins = plugins or []

        super().__init__(agents)

        # Keep triage reference for backward compatibility (first agent)
        self.triage = self.team_agents[0]
        
        self._is_initialized = False

    # Plugin lifecycle hooks
    async def _call_plugin_hook(self, hook_name: str, *args, **kwargs):
        """Call a lifecycle hook on all plugins.
        
        Args:
            hook_name: Name of the hook method to call
            *args, **kwargs: Arguments to pass to the hook
        """
        for plugin in self.plugins:
            hook_method = getattr(plugin, hook_name, None)
            if hook_method:
                await run_func(hook_method, *args, **kwargs)



    def get_active_agent(self, memory: Memory) -> Agent | RemoteAgent:
        active_agent_name = memory.extra_data.get("active_agent")
        if (active_agent_name is None) or (active_agent_name not in self.agents):
            active_agent_name = list(self.agents.keys())[0]
            logger.debug(
                f"Active agent not found in memory, setting to {active_agent_name}"
            )
            memory.extra_data["active_agent"] = active_agent_name
        active_agent = self.agents[active_agent_name]
        return active_agent

    def set_active_agent(self, memory: Memory, agent_name: str):
        memory.extra_data["active_agent"] = agent_name

    async def add_list_agents_tool(self):
        """Add list_agents() tool to all agents."""

        def get_agents_info(exclude_slug: str | None = None) -> list[dict]:
            """Get info for all agents except the one with exclude_slug."""
            agents_info = []
            for agent_name, agent in self.agents.items():
                slug = _slugify(agent_name)
                if slug == exclude_slug:
                    continue
                info = {"name": slug}
                if hasattr(agent, "description") and agent.description:
                    info["description"] = agent.description
                agents_info.append(info)
            return agents_info

        for agent in self.team_agents:
            caller_slug = _slugify(agent.name)

            def make_list_agents(exclude: str):
                """Create list_agents function with caller excluded."""

                def list_agents():
                    """List all available agents and their capabilities."""
                    agents_info = get_agents_info(exclude_slug=exclude)
                    if not agents_info:
                        return "No other agents available."

                    output = "**Available Agents:**\n\n"
                    for info in agents_info:
                        output += f"- **{info['name']}**"
                        if "description" in info:
                            output += f": {info['description']}"
                        output += "\n"

                    return output

                return list_agents

            list_agents_func = make_list_agents(caller_slug)
            list_agents_func.__name__ = "list_agents"
            list_agents_func.__name__ = "list_agents"
            list_agents_func.__doc__ = LIST_AGENTS_DOC

            await run_func(agent.tool, list_agents_func)

    async def add_unified_call_agent_tool(self):
        """Add unified call_agent(agent_name, instruction) tool for agents."""

        async def _add_call_agent_tool_to_agent(
            calling_agent: Agent | RemoteAgent,
        ):
            """Add call_agent() tool to an agent."""

            async def call_agent(
                agent_name: str,
                instruction: str,
                context_variables: dict | None = None,
            ):
                """Delegate a task to another agent in the team.

                Args:
                    agent_name: Name of the target agent to delegate to.
                    instruction: Clear task description for the target agent.

                Returns:
                    Response content produced by the target agent.
                """
                target_agent = self.get_target_agent(agent_name, instruction)
                run_context = get_current_run_context()

                # Shallow copy context_variables to avoid mutating the original
                context_variables = dict(context_variables or {})
                
                # CRITICAL FIX: Deep copy _metadata to prevent child call pollution
                if "_metadata" in context_variables:
                    context_variables["_metadata"] = copy.deepcopy(context_variables["_metadata"])
                
                # P2: _build_child_context_metadata now returns (metadata, execution_context_id)
                child_metadata, execution_context_id = _build_child_context_metadata(
                    run_context,
                    target_agent,
                    context_variables,
                    self.max_delegate_depth,
                )

                child_context_variables = dict(context_variables)
                child_context_variables["_metadata"] = child_metadata
                # P2: Set execution_context_id at top level for child agent
                child_context_variables["execution_context_id"] = execution_context_id
                child_run_overrides, child_context_variables = (
                    _get_cache_safe_child_run_overrides(
                        run_context,
                        target_agent,
                        child_context_variables,
                    )
                )
                # CC-style delegation: structured fork is PRIMARY path.
                # Child receives parent's full optimized message history as
                # structured messages (forkContextMessages), enabling prompt
                # cache sharing.  Summary is only a FALLBACK when no
                # structured context is available.
                use_summary_fallback = False
                fork_context_messages = await _get_cache_safe_child_fork_context_messages(
                    run_context,
                    target_agent,
                )
                if fork_context_messages:
                    # Path 1: Cache-compatible — share parent prefix byte-for-byte
                    child_context_variables["_cache_safe_fork_context_messages"] = (
                        fork_context_messages
                    )
                elif run_context.memory:
                    # Path 2: Incompatible agents — pass optimized structured
                    # messages (CC's forkContextMessages for non-cache-sharing)
                    structured_fork = _build_structured_fork_context(run_context)
                    if structured_fork:
                        child_context_variables["_cache_safe_fork_context_messages"] = (
                            structured_fork
                        )
                    else:
                        # Path 3: No structured context available — fall back to
                        # summary (only when use_summary=True)
                        use_summary_fallback = self.use_summary
                else:
                    use_summary_fallback = self.use_summary

                # Build task message — with or without summary
                task_message = await create_delegation_task_message(
                    history=run_context.memory.get_messages(None)
                    if run_context.memory
                    else [],
                    instruction=instruction,
                    use_summary=use_summary_fallback,
                )
                if not task_message:
                    return ""

                parent_step_hook = run_context.process_step_message
                parent_chunk_hook = run_context.process_chunk

                async def wrapped_step(step_message: dict):
                    # P2: Set execution_context_id at message top level
                    if "execution_context_id" not in step_message:
                        step_message["execution_context_id"] = execution_context_id
                    
                    # P0 FIX + P2: Merge metadata using setdefault chaining
                    step_message.setdefault("_metadata", child_metadata).setdefault(
                        "chain_path", child_metadata["chain_path"]
                    )
                    
                    if parent_step_hook is not None:
                        await run_func(parent_step_hook, step_message)

                async def wrapped_chunk(chunk: dict):
                    # P2: Set execution_context_id at message top level
                    if "execution_context_id" not in chunk:
                        chunk["execution_context_id"] = execution_context_id
                    
                    # P0 FIX + P2: Merge metadata using setdefault chaining
                    chunk.setdefault("_metadata", child_metadata).setdefault(
                        "chain_path", child_metadata["chain_path"]
                    )
                    
                    if parent_chunk_hook is not None:
                        await run_func(parent_chunk_hook, chunk)

                child_memory = Memory(
                    name=f"{target_agent.name}-{execution_context_id}"
                )

                response = await target_agent.run(
                    task_message,
                    memory=child_memory,
                    use_memory=False,
                    update_memory=True,  # Must be True for ACE learning to capture messages
                    process_step_message=wrapped_step,
                    process_chunk=wrapped_chunk,
                    execution_context_id=execution_context_id,
                    context_variables=child_context_variables,
                    allow_transfer=False,
                    **child_run_overrides,
                )

                # Submit sub_agent learning via plugin hooks
                # Use parent memory's id for consistent chat grouping
                parent_chat_id = (
                    getattr(run_context.memory, "id", "")
                    if run_context.memory
                    else ""
                )
                sub_agent_result = {
                    "agent_name": target_agent.name,
                    "messages": child_memory._messages,
                    "chat_id": parent_chat_id,
                    "question": instruction,  # For sub-agents, include the delegation instruction
                }
                await self._call_plugin_hook("on_run_end", self, sub_agent_result)

                content = response.content if response else ""
                return content

            call_agent.__name__ = "call_agent"
            call_agent.__name__ = "call_agent"
            call_agent.__doc__ = CALL_AGENT_DOC

            await run_func(calling_agent.tool, call_agent)

        for agent in self.team_agents:
            await _add_call_agent_tool_to_agent(agent)

    async def add_transfer_tools_to_agents(self):
        """Add transfer tool to all agents for inter-agent communication."""

        def make_transfer_func(source_name: str):
            """Create a transfer function with source agent name captured."""

            def transfer_to_agent(target_name: str):
                """Transfer control to another agent by name.

                Args:
                    target_name: Name of the agent to transfer to.
                """
                # Normalize target_name to match agent lookup
                normalized = target_name.replace(" ", "_").lower()
                agent_map = {
                    aname.replace(" ", "_").lower(): agent
                    for aname, agent in self.agents.items()
                }
                if normalized not in agent_map:
                    raise ValueError(f"Unknown agent: {target_name}")
                if normalized == source_name.replace(" ", "_").lower():
                    raise ValueError("Cannot transfer to self")
                return agent_map[normalized]

            return transfer_to_agent

        for source_agent in self.team_agents:
            transfer_func = make_transfer_func(source_agent.name)
            await run_func(source_agent.tool, transfer_func)

    async def async_setup(self):
        """Setup team by enabling inter-agent tools when multiple agents exist.

        Tools added when len(agents) > 1:
        - transfer_to_agent(): Transfer control to another agent (if allow_transfer)
        - list_agents(): Discover other agents in the team
        - call_agent(): Delegate tasks to other agents
        
        Also calls on_team_created hook for all plugins.
        """
        if self._is_initialized:
            return

        if len(self.team_agents) > 1:
            if self.allow_transfer:
                await self.add_transfer_tools_to_agents()
            await self.add_list_agents_tool()
            await self.add_unified_call_agent_tool()
        
        # Call plugin lifecycle hook
        await self._call_plugin_hook("on_team_created", self)
        
        self._is_initialized = True

    async def run(self, msg: AgentInput, memory: Memory | None = None, **kwargs):
        await self.async_setup()
        if memory is None:
            memory = Memory(name="pantheon-team")

        # Call on_run_start hook for plugins
        run_context = {
            "memory": memory,
            "kwargs": kwargs,
        }
        await self._call_plugin_hook("on_run_start", self, msg, run_context)

        # Record turn start for learning
        turn_start_index = len(memory._messages)

        while True:
            active_agent = self.get_active_agent(memory)

            resp = await active_agent.run(msg, memory=memory, **kwargs)
            if isinstance(resp, AgentTransfer):
                transfer_call_id = resp.tool_call_id
                logger.info(
                    f"[TRANSFER] {active_agent.name} -> {resp.to_agent} | tool_call_id: {transfer_call_id}"
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": transfer_call_id
                    or ("call_" + str(uuid.uuid4())[:20]),
                    "tool_name": "transfer",
                    "content": resp.to_agent,
                }
                # Switch to target agent and continue loop with tool_message
                self.set_active_agent(memory, resp.to_agent)
                msg = tool_message
            else:
                # Call on_run_end hook for plugins (main agent learning)
                current_messages = [
                    m
                    for m in memory._messages[turn_start_index:]
                    if m.get("execution_context_id") is None
                ]
                if current_messages:
                    run_result = {
                        "agent_name": active_agent.name,
                        "messages": current_messages,
                        "chat_id": memory.id or "",
                    }
                    await self._call_plugin_hook("on_run_end", self, run_result)
                return resp

    def get_target_agent(self, agent_name: str, instruction: str) -> Agent:
        """Get target agent by name for delegation.

        Args:
            agent_name: Name of the target agent (supports slug format).
            instruction: Task instruction (must not be empty).

        Returns:
            The target Agent instance.
        """
        # Normalize agent name (e.g., "Data Analyst" -> "data_analyst")
        all_agents = {
            aname.replace(" ", "_").lower(): agent
            for aname, agent in self.agents.items()
        }
        agent_name = agent_name.replace(" ", "_").lower()

        if agent_name not in all_agents:
            raise ValueError(
                f"Agent '{agent_name}' not found. Available: {list(all_agents.keys())}"
            )

        if not instruction or not instruction.strip():
            raise ValueError("Instruction cannot be empty")

        target_agent = all_agents[agent_name]
        if not isinstance(target_agent, Agent):
            raise NotImplementedError(
                "call_agent currently supports only local Agent instances"
            )
        return target_agent

    async def force_compress(self, memory: "Memory") -> dict:
        """
        Force context compression regardless of threshold.
        
        This method delegates to CompressionPlugin if available.
        Used by REPL and ChatRoom for manual compression.
        
        Args:
            memory: Memory instance to compress
            
        Returns:
            dict with compression result info: {success: bool, message: str, ...}
        """
        # Find CompressionPlugin in plugins
        for plugin in self.plugins:
            if hasattr(plugin, "force_compress"):
                return await plugin.force_compress(self, memory)
        
        return {"success": False, "message": "Compression plugin not available"}



DELEGATION_RECENT_TAIL_SIZE = 20


async def create_delegation_task_message(
    history: list[dict],
    instruction: str,
    use_summary: bool = True,
) -> str | None:
    """Create a delegated task message with summary-first, on-demand-detail strategy.

    When *use_summary* is True (default):
      1. Generate a compact LLM summary of the full history.
      2. Pass only the **recent tail** of the history to
         ``build_delegation_context_message`` — this avoids embedding the entire
         parent conversation in the child prompt.
      3. Append an on-demand hint so the child agent knows it can retrieve
         full tool outputs from disk if needed.

    When *use_summary* is False (explicit opt-out):
      Only the raw *instruction* is returned — no history or summary.
    """
    if not instruction:
        return None

    if not use_summary:
        return instruction

    # --- summary-first: generate compact summary from full history -----------
    summary_text = None
    if history:
        try:
            from pantheon.chatroom.special_agents import get_summary_generator

            summary_gen = get_summary_generator()
            summary_text = await summary_gen.generate_summary(history, max_tokens=1000)
        except Exception as e:
            logger.warning(f"Failed to generate summary for delegation: {e}")

    # --- only pass the recent tail to build_delegation_context_message --------
    # The summary covers older context; recent messages provide necessary detail.
    recent_history = history[-DELEGATION_RECENT_TAIL_SIZE:] if history else []

    from pantheon.utils.token_optimization import build_delegation_context_message

    return build_delegation_context_message(
        history=recent_history,
        instruction=instruction,
        summary_text=summary_text,
    )
