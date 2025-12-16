import re
import uuid
from typing import TYPE_CHECKING, List, Optional

from ..agent import (
    Agent,
    AgentInput,
    AgentTransfer,
    RemoteAgent,
    get_current_run_context,
)
from ..memory import Memory
from ..utils.log import logger
from ..utils.misc import run_func
from .base import Team

if TYPE_CHECKING:
    from ..ace import ACELearningPipeline, Skillbook


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "agent"


def _build_execution_context_id(
    parent_metadata: dict,
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
        execution_context_id = parent_metadata.get("execution_context_id")
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
    if parent_path:
        chain_path = list(parent_path)
    else:
        chain_path = [run_context.agent.name]
    if target_agent.name in chain_path:
        raise RuntimeError(
            "Delegation loop detected: this agent already appears in the current chain."
        )
    chain_path.append(target_agent.name)

    child_depth = max(len(chain_path) - 1, 0)
    if child_depth > max_depth:
        raise RuntimeError("Delegation depth limit reached.")

    execution_context_id = _build_execution_context_id(
        parent_metadata, run_context, child_depth, target_agent.name
    )

    tool_call_id = context_variables.get("tool_call_id") or (
        "call_" + uuid.uuid4().hex[:12]
    )

    metadata = {
        "execution_context_id": execution_context_id,
        "chain_path": chain_path,
        "parent": {
            "agent": run_context.agent.name,
            "call_id": tool_call_id,
        },
    }

    return metadata


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
        use_summary: bool = False,
        max_delegate_depth: int | None = 5,
        allow_transfer: bool = True,
        skillbook: Optional["Skillbook"] = None,
        ace_pipeline: Optional["ACELearningPipeline"] = None,
    ):
        """Initialize PantheonTeam with unified agent architecture.

        Args:
            agents: List of agents in the team.
            use_summary: If True, generate and prepend context summary
                         when delegating tasks.
            max_delegate_depth: Maximum depth for nested call_agent calls.
            allow_transfer: If True, add transfer_to_agent tool to agents.
            skillbook: Optional ACE Skillbook for long-term memory.
            ace_pipeline: Optional ACE learning pipeline.

        Note:
            All agents are equal - the first one is used as the default
            entry point but receives no special treatment.
        """
        if not agents:
            raise ValueError("Team must have at least one agent")

        self.team_agents = agents
        self.use_summary = use_summary
        self.max_delegate_depth = max_delegate_depth
        self.allow_transfer = allow_transfer
        
        # ACE long-term memory
        self._skillbook = skillbook
        self._ace_pipeline = ace_pipeline
        self._skills_injected = False  # Track if skills are already injected
        
        # Context compression
        self._compressor = self._init_compressor()

        super().__init__(agents)

        # Keep triage reference for backward compatibility (first agent)
        self.triage = self.team_agents[0]
        # Note: Skillbook injection moved to first run() call for cache-friendly behavior
    
    def _init_compressor(self):
        """Initialize context compressor from settings."""
        from ..settings import get_settings
        from ..compression import CompressionConfig, ContextCompressor
        
        settings = get_settings()
        compression_config = settings.get_compression_config()
        
        if not compression_config.get("enable", False):
            return None
        
        config = CompressionConfig(
            enable=True,
            threshold=compression_config.get("threshold", 0.8),
            preserve_recent_messages=compression_config.get("preserve_recent_messages", 5),
            max_tool_arg_length=compression_config.get("max_tool_arg_length", 2000),
            max_tool_output_length=compression_config.get("max_tool_output_length", 5000),
            retry_after_messages=compression_config.get("retry_after_messages", 10),
        )
        
        # Use first agent's model for compression
        model = self.team_agents[0].models[0] if self.team_agents else "low"
        
        return ContextCompressor(config, model)

    def _inject_skillbook_to_agents(self) -> None:
        """Inject relevant skillbook content into all agent instructions."""
        for agent in self.team_agents:
            self._inject_skillbook_to_agent(agent)

    def _inject_skillbook_to_agent(self, agent) -> None:
        """Inject skillbook content into a single agent's instructions."""
        if not self._skillbook:
            return
        
        # Check for any skillbook header to avoid duplicate injection
        if "📌 User Rules" in agent.instructions or "📚 Learned" in agent.instructions:
            return
        
        skills_prompt = self._skillbook.as_prompt(agent.name)
        if skills_prompt:
            agent.instructions += f"\n\n{skills_prompt}"
            logger.debug(f"Injected skillbook into agent: {agent.name}")

    def _submit_learning(
        self,
        agent_name: str,
        messages: List[dict],
        parent_question: Optional[str] = None,
    ) -> None:
        """Submit learning data to ACE pipeline.
        
        Args:
            agent_name: Name of the agent that produced the trajectory
            messages: List of messages from the conversation
            parent_question: For sub_agent, the delegation instruction
        """
        from ..ace.pipeline import build_learning_input
        from ..settings import get_settings
        
        settings = get_settings()
        ace_config = settings.get_ace_config()
        
        turn_id = str(uuid.uuid4())
        learning_input = build_learning_input(
            turn_id=turn_id,
            agent_name=agent_name,
            messages=messages,
            learning_dir=ace_config["learning_dir"],
            max_tool_arg_length=ace_config["max_tool_arg_length"],
            max_tool_output_length=ace_config["max_tool_output_length"],
        )
        
        # For sub_agent, use delegation instruction as question
        if parent_question:
            learning_input.question = parent_question
        
        self._ace_pipeline.submit(learning_input)
        logger.debug(f"Submitted learning for {agent_name}, turn_id={turn_id}")

    async def _perform_compression(self, memory: Memory) -> None:
        """Perform context compression on the memory.
        
        Args:
            memory: Memory instance to compress
        """
        from ..settings import get_settings
        
        settings = get_settings()
        # Use ace/learning directory for unified management with ACE learning data
        ace_config = settings.get_ace_config()
        compression_dir = ace_config["learning_dir"]
        
        result = await self._compressor.compress(
            messages=memory._messages,
            compression_dir=compression_dir,
        )
        
        if result.compression_message:
            # Get compression range to know which messages to replace
            compress_start, compress_end = self._compressor._get_compression_range(memory._messages)
            
            # Non-destructive compression: Insert compression message AFTER the compressed block
            # This preserves raw messages for UI/History while allowing get_messages(for_llm=True)
            # to filter them out based on the checkpoint position.
            
            # Insert at compress_end (the index immediately following the compressed block)
            new_messages = (
                memory._messages[:compress_end] +
                [result.compression_message] +
                memory._messages[compress_end:]
            )
            memory._messages = new_messages
            
            logger.info(
                f"Context compression checkpoint inserted at index {compress_end}. "
                f"Compressed {compress_end - compress_start} messages ({result.original_tokens} -> {result.new_tokens} tokens)."
            )

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
            list_agents_func.__doc__ = (
                "List all available agents and their capabilities.\n\n"
                "Returns a list of agent names and descriptions. Use this to:\n"
                "- Discover agents you can delegate to via call_agent()\n"
                "- Transfer control to via transfer_to_agent()\n"
                "- Choose the right agent for specific task types\n\n"
                "Call this before delegating if unsure which agent handles a task."
            )

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

                context_variables = dict(context_variables or {})
                child_metadata = _build_child_context_metadata(
                    run_context,
                    target_agent,
                    context_variables,
                    self.max_delegate_depth,
                )
                execution_context_id = child_metadata["execution_context_id"]

                child_context_variables = dict(context_variables)
                child_context_variables["_metadata"] = child_metadata

                # Build task message with optional history summary
                task_message = await create_delegation_task_message(
                    history=run_context.memory.get_messages(None)
                    if run_context.memory
                    else [],
                    instruction=instruction,
                    use_summary=self.use_summary,
                )
                if not task_message:
                    return ""

                parent_step_hook = run_context.process_step_message
                parent_chunk_hook = run_context.process_chunk

                async def wrapped_step(step_message: dict):
                    metadata = step_message.get("_metadata")
                    if not metadata or "execution_context_id" not in metadata:
                        step_message["_metadata"] = child_metadata
                    if parent_step_hook is not None:
                        await run_func(parent_step_hook, step_message)

                async def wrapped_chunk(chunk: dict):
                    metadata = chunk.get("_metadata")
                    if not metadata or "execution_context_id" not in metadata:
                        chunk["_metadata"] = child_metadata
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
                )

                # Submit sub_agent learning (child_memory is the complete conversation)
                if self._ace_pipeline:
                    self._submit_learning(
                        agent_name=target_agent.name,
                        messages=child_memory._messages,
                        parent_question=instruction,
                    )

                content = response.content if response else ""
                return content

            call_agent.__name__ = "call_agent"
            call_agent.__doc__ = (
                "Delegate a task to another agent in the team.\n\n"
                "Args:\n"
                "    agent_name: Name of the target agent (use list_agents() to discover).\n"
                "    instruction: Task description with the following structure:\n"
                "        - **Goal**: What needs to be accomplished and why it matters.\n"
                "        - **Context**: All background the agent needs (files, data, constraints).\n"
                "          Assume the agent has no memory of prior conversation.\n"
                "        - **Expected Outcome**: Format, files, or deliverables expected.\n\n"
                "Returns:\n"
                "    Response content from the target agent.\n\n"
                "Example instruction:\n"
                "    'Goal: Analyze gene expression patterns in the PBMC dataset.\\n\\n'\n"
                "    'Context: Dataset at /data/pbmc.h5ad, already preprocessed. '\n"
                "    'Focus on T cell subpopulations.\\n\\n'\n"
                "    'Expected Outcome: Report with UMAP visualization and marker genes.'"
            )

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
        """
        if len(self.team_agents) > 1:
            if self.allow_transfer:
                await self.add_transfer_tools_to_agents()
            await self.add_list_agents_tool()
            await self.add_unified_call_agent_tool()

    async def run(self, msg: AgentInput, memory: Memory | None = None, **kwargs):
        await self.async_setup()
        if memory is None:
            memory = Memory(name="pantheon-team")
        
        # Inject skillbook on first run (cache-friendly: avoids re-injection)
        if self._skillbook and not self._skills_injected:
            self._inject_skillbook_to_agents()
            self._skills_injected = True
            logger.debug("Skillbook injected into agents on first run")
        
        # Record turn start for learning
        turn_start_index = len(memory._messages)
        
        while True:
            active_agent = self.get_active_agent(memory)
            
            # Check and perform compression if needed
            if self._compressor and self._compressor.should_compress(memory._messages, active_agent.models[0]):
                await self._perform_compression(memory)
            
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
                # Submit main agent learning (exclude sub_agent messages)
                if self._ace_pipeline:
                    current_messages = [
                        m for m in memory._messages[turn_start_index:]
                        if m.get("execution_context_id") is None
                    ]
                    if current_messages:
                        self._submit_learning(
                            agent_name=active_agent.name,
                            messages=current_messages,
                        )
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
                f"Agent '{agent_name}' not found. "
                f"Available: {list(all_agents.keys())}"
            )

        if not instruction or not instruction.strip():
            raise ValueError("Instruction cannot be empty")

        target_agent = all_agents[agent_name]
        if not isinstance(target_agent, Agent):
            raise NotImplementedError(
                "call_agent currently supports only local Agent instances"
            )
        return target_agent


async def create_delegation_task_message(
    history: list[dict],
    instruction: str,
    use_summary: bool = True,
) -> str | None:
    """Create a delegated task message with optional summary context."""
    if not instruction:
        return None

    # If summary is disabled, the instruction is the entire content.
    if not use_summary:
        return instruction

    # Default behavior: Summarize history and append the instruction.
    summary_text = None
    if history:
        try:
            from ..chatroom.special_agents import get_summary_generator

            summary_gen = get_summary_generator()
            summary_text = await summary_gen.generate_summary(history, max_tokens=1000)
        except Exception as e:
            logger.warning(f"Failed to generate summary for delegation: {e}")

    content_parts = []
    if summary_text:
        content_parts.append(f"Context Summary:\n{summary_text}")
    content_parts.append(f"Task: {instruction}")
    return "\n\n".join(content_parts)
