import time
import uuid

from ..agent import (
    Agent,
    AgentInput,
    AgentTransfer,
    RemoteAgent,
    get_current_run_context,
)
from ..constant import SystemPromptMode
from ..memory import Memory
from ..utils.log import logger
from ..utils.misc import run_func
from .base import Team


class PantheonTeam(Team):
    """Pantheon team structure with two types of agents.

    Unified architecture with no special agent roles:
    - Inline Agents: Defined in agents_config
      * All treated equally - all have list_agents(), call_agent(), transfer_to_*()
      * Can control their own execution and delegate to other agents
    - Sub-Agents: Loaded from agents.yaml library
      * Computation frameworks/tools
      * Support: call_agent() from inline agents only
      * Stateless, reusable, cannot be transferred to

    Features enabled based on composition:
    - Agent Transfer: When multiple inline agents (len > 1)
    - Sub-Agent Discovery: When sub_agents are loaded
    """

    def __init__(
        self,
        inline_agents: list[Agent | RemoteAgent],
        sub_agents: list[Agent | RemoteAgent] = None,
    ):
        """Initialize PantheonTeam with clear agent type separation.

        Args:
            inline_agents: Agents from agents_config
            sub_agents: Agents loaded from agents.yaml library (computation frameworks)

        Note:
            All inline agents are equal - the first one is commonly called triage for convention,
            but receives no special treatment or capabilities.
        """
        if not inline_agents:
            raise ValueError("Team must have at least one inline agent")

        self.inline_agents = inline_agents  # All inline agents
        self.sub_agents = sub_agents or []  # Track sub-agents separately

        # Initialize parent with all agents (inline + sub)
        all_agents = inline_agents + (sub_agents or [])
        super().__init__(all_agents)

        # Mark which agents are inline vs sub (used to determine tool availability)
        self._inline_agent_names: set[str] = {a.name for a in inline_agents}
        self._sub_agent_names: set[str] = {a.name for a in self.sub_agents}
        # Determine which features to enable based on team composition
        self.has_transfer_agents = len(inline_agents) > 1  # More than just one agent
        self.has_sub_agents = len(self.sub_agents) > 0

        # Mark all inline agents as capable of delegating if there are other agents
        for agent in inline_agents:
            if isinstance(agent, Agent):
                agent.can_delegate = self.has_transfer_agents or self.has_sub_agents

        # Configure sub-agents to use SUBAGENT mode (streamlined for direct execution)
        # and ensure they cannot delegate to other agents
        for agent in self.sub_agents:
            if isinstance(agent, Agent):
                # Force SUBAGENT mode for sub-agents
                agent.system_prompt_mode = SystemPromptMode.SUBAGENT
                # Prevent sub-agents from delegating (no second-level delegation)
                agent.can_delegate = False

        # Keep triage reference for backward compatibility (it's the first inline agent)
        self.triage = inline_agents[0]

    def get_active_agent(self, memory: Memory) -> Agent | RemoteAgent:
        active_agent_name = memory.extra_data.get("active_agent")
        if (active_agent_name is None) or (active_agent_name not in self.agents):
            active_agent_name = list(self.agents.keys())[0]
            logger.warning(
                f"Active agent not found in memory, setting to {active_agent_name}"
            )
            memory.extra_data["active_agent"] = active_agent_name
        active_agent = self.agents[active_agent_name]
        return active_agent

    def set_active_agent(self, memory: Memory, agent_name: str):
        memory.extra_data["active_agent"] = agent_name

    def list_agents_descriptions(self) -> list[dict]:
        """Get structured information about all available sub-agents.

        Returns only sub-agent name and description - inline agents use this
        to discover which agents can be delegated to via call_agent().

        Returns:
            List of dicts with sub-agent name and description only.
        """
        agents_info = []
        for agent_name, agent in self.agents.items():
            # Only include sub-agents (not inline agents)
            if agent_name not in self._sub_agent_names:
                continue
            # convert agent name to lower case
            agent_name = agent_name.replace(" ", "_").lower()
            agent_info = {
                "name": agent_name,
            }

            # Add description if available (main focus for parent agent)
            if hasattr(agent, "description") and agent.description:
                agent_info["description"] = agent.description

            agents_info.append(agent_info)

        return agents_info

    async def add_list_agents_tool(self):
        """Add list_agents() tool to all inline agents.

        This tool allows inline agents to dynamically discover available sub-agents
        at runtime without hardcoded knowledge of the team composition.
        """

        def list_agents():
            """List all available sub-agents and their capabilities."""
            agents_info = self.list_agents_descriptions()
            if not agents_info:
                return "No sub-agents available."

            # Format for readability - show name and description only
            output = "**Available Sub-Agents:**\n\n"
            for agent in agents_info:
                output += f"- **{agent['name']}**"
                if "description" in agent:
                    output += f": {agent['description']}"
                output += "\n"

            return output

        list_agents.__name__ = "list_agents"
        list_agents.__doc__ = (
            "List all available sub-agents and their capabilities. "
            "Use this to understand which agents you can delegate to via call_agent(). "
            "Returns agent names and descriptions of their expertise."
        )

        # Add list_agents() to all inline agents (not just triage)
        for agent in self.inline_agents:
            await run_func(agent.tool, list_agents)

    async def add_unified_call_agent_tool(self):
        """Add unified call_agent(agent_name, instruction) tool for inline agents.

        This tool allows inline agents to delegate tasks to sub-agents in the team.
        Only inline agents get this capability - sub-agents are computation frameworks.
        call_agent() can ONLY target sub-agents, not other inline agents.
        """

        async def _add_call_agent_tool_to_inline_agent(
            calling_agent: Agent | RemoteAgent,
        ):
            """Add call_agent() tool to an inline agent."""

            async def call_agent(
                agent_name: str,
                instruction: str,
                context_variables: dict | None = None,
            ):
                """Delegate a task to a sub-agent in the team.

                Args:
                    agent_name: Name of the target sub-agent to delegate to
                    instruction: Clear task description for the target agent

                Returns:
                    Response content produced by the delegated sub-agent.
                """
                target_agent = self.get_taget_agent(agent_name, instruction)
                run_context = get_current_run_context()
                if run_context is None:
                    raise RuntimeError(
                        "call_agent must be executed within an active agent tool call"
                    )

                context_variables = dict(context_variables or {})
                context_variables.setdefault("parent_agent", run_context.agent.name)

                parent_step_hook = run_context.process_step_message
                parent_chunk_hook = run_context.process_chunk

                def update_metadata(data: dict):
                    metadata = dict(data.get("metadata") or {})
                    metadata["tool_call_id"] = context_variables.get("tool_call_id")
                    metadata["parent_agent"] = run_context.agent.name
                    metadata["sub_agent"] = target_agent.name
                    data["metadata"] = metadata

                async def wrapped_step(step_message: dict):
                    update_metadata(step_message)
                    if parent_step_hook is not None:
                        await run_func(parent_step_hook, step_message)

                async def wrapped_chunk(chunk: dict):
                    update_metadata(chunk)
                    if parent_chunk_hook is not None:
                        await run_func(parent_chunk_hook, chunk)

                # Build history summary for sub-agent task message

                task_message = await create_delegation_task_message(
                    history=run_context.memory.get_messages(None)
                    if run_context.memory
                    else [],
                    instruction=instruction,
                )
                if task_message is None:
                    return ""

                execution_context_id = f"ctx_{str(uuid.uuid4())[:12]}"
                child_memory = Memory(
                    name=f"{target_agent.name}-{execution_context_id}"
                )
                response = await target_agent.run(
                    [task_message],
                    memory=child_memory,
                    use_memory=False,
                    update_memory=False,
                    process_step_message=wrapped_step,
                    process_chunk=wrapped_chunk,
                    execution_context_id=execution_context_id,
                    context_variables=context_variables,
                    allow_transfer=False,
                )

                if isinstance(response, AgentTransfer):
                    raise RuntimeError(
                        "Sub-agent attempted to transfer execution, which is disabled "
                        "during call_agent delegated runs."
                    )

                content = response.content if response else ""
                return content

            # Set proper function metadata for LLM
            call_agent.__name__ = "call_agent"
            call_agent.__doc__ = (
                "Delegate a task to a sub-agent in the team. "
                "Pass the agent_name and clear instruction describing context, what to do and the desired output. "
            )

            # Register tool
            await run_func(calling_agent.tool, call_agent)

        # Add call_agent() to all inline agents (not sub-agents)
        for agent in self.inline_agents:
            await _add_call_agent_tool_to_inline_agent(agent)

    async def add_transfer_tools_to_inline_agents(self):
        """Add transfer_to_* tools to all inline agents for inter-agent communication.

        Each inline agent can transfer to other inline agents (not sub-agents).
        This enables agents to hand off tasks to each other.
        """
        # For each target agent, create a transfer function and register it with all source agents
        for target_agent in self.inline_agents:
            # Create transfer function using closure
            # Capture target_agent.name via default parameter to avoid late binding issues
            def transfer_func(
                target_name: str = target_agent.name,
            ):
                """Transfer to the target agent."""
                return self.agents[target_name]

            agent_func_name = target_agent.name.replace(" ", "_").lower()
            func_name = f"transfer_to_{agent_func_name}"
            transfer_func.__name__ = func_name
            transfer_func.__doc__ = f"Transfer to {target_agent.name}."

            # Register this transfer function with all source agents (except the target itself)
            for source_agent in self.inline_agents:
                if source_agent.name == target_agent.name:
                    continue  # Can't transfer to self

                # Register the same function as a tool for each source agent
                await run_func(source_agent.tool, transfer_func)

    async def async_setup(self):
        """Setup team by enabling appropriate tools based on team composition.

        All agents are already initialized in __init__.
        This method adds tools based on feature enablement:
        - transfer_to_*(): Inline agents can transfer to other inline agents
        - list_agents(): All inline agents can discover available sub-agents
        - call_agent(): All inline agents can delegate tasks to sub-agents
        """
        # Add transfer_to_* tools to enable inline agent communication
        if self.has_transfer_agents:
            await self.add_transfer_tools_to_inline_agents()

        # Add list_agents() to all inline agents if there are sub-agents to discover
        if self.has_sub_agents:
            await self.add_list_agents_tool()

        # Add call_agent() to all inline agents if there are agents to delegate to
        if self.has_transfer_agents or self.has_sub_agents:
            await self.add_unified_call_agent_tool()

    async def run(self, msg: AgentInput, memory: Memory | None = None, **kwargs):
        await self.async_setup()
        if memory is None:
            memory = Memory(name="pantheon-team")
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
                return resp

    def get_taget_agent(self, agent_name: str, instruction: str) -> Agent:
        # real agent name is like : Data Analyst, but the llm may call with data_analyst
        all_agents = {
            aname.replace(" ", "_").lower(): agent
            for aname, agent in self.agents.items()
        }
        agent_name = agent_name.replace(" ", "_").lower()
        # Validate agent exists and is a sub-agent
        if agent_name not in all_agents:
            raise ValueError(
                f"Agent '{agent_name}' not found. Available agents: {list(all_agents.keys())}"
            )

        if not instruction or not instruction.strip():
            raise ValueError("Instruction cannot be empty")

        target_agent = all_agents[agent_name]
        if target_agent.name not in self._sub_agent_names:
            raise ValueError(
                f"Agent '{agent_name}' is not a sub-agent. "
                f"Sub-agents: {sorted(self._sub_agent_names)}"
            )
        if not isinstance(target_agent, Agent):
            raise NotImplementedError(
                "call_agent currently supports only local Agent instances"
            )
        return target_agent


async def create_delegation_task_message(
    history: list[dict],
    instruction: str,
) -> dict | None:
    """Create a delegated task message with optional summary context."""
    if not instruction:
        return None

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
    combined_content = "\n\n".join(content_parts)

    return {
        "role": "user",
        "content": combined_content,
        "timestamp": time.time(),
        "id": str(uuid.uuid4()),
    }
