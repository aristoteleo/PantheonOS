import asyncio
import uuid
from abc import ABC

from .agent import Agent, AgentInput, AgentResponse, AgentTransfer, RemoteAgent
from .constant import SystemPromptMode
from .memory import Memory
from .utils.log import logger
from .utils.misc import run_func


class Team(ABC):
    def __init__(self, agents: list[Agent | RemoteAgent]):
        self.agents = {}
        for agent in agents:
            self.agents[agent.name] = agent
        self.events_queue = asyncio.Queue()

    async def async_setup(self):
        pass

    async def gather_events(self):
        async def _gather_agent_events(agent: Agent | RemoteAgent):
            while True:
                event = await agent.events_queue.get()
                new_event = {
                    "agent_name": agent.name,
                    "event": event,
                }
                self.events_queue.put_nowait(new_event)

        tasks = []
        for agent in self.agents.values():
            tasks.append(_gather_agent_events(agent))
        await asyncio.gather(*tasks)

    async def run(self, msg: AgentInput, **kwargs):
        pass

    async def chat(self, message: str | dict | None = None):
        """Chat with the team with a REPL interface."""
        from .repl.team import Repl

        repl = Repl(self)
        await repl.run(message)


class SwarmTeam(Team):
    """Team that run agents in handoff & routines patterns like
    OpenAI's [Swarm framework](https://github.com/openai/swarm).
    """

    def __init__(self, agents: list[Agent | RemoteAgent]):
        super().__init__(agents)

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

    async def run(self, msg: AgentInput, memory: Memory | None = None, **kwargs):
        if memory is None:
            memory = Memory(name="swarm-team")
        while True:
            active_agent = self.get_active_agent(memory)
            resp = await active_agent.run(msg, memory=memory, **kwargs)
            if isinstance(resp, AgentTransfer):
                self.set_active_agent(memory, resp.to_agent)
                msg = resp
            else:
                return resp


class SwarmCenterTeam(SwarmTeam):
    """Swarm team that has a central triage agent that decides which agent to handoff to."""

    def __init__(self, triage: Agent, agents: list[Agent | RemoteAgent]):
        super().__init__([triage])
        self.triage = triage
        self._agents_to_add = agents

    async def add_agent(self, agent: Agent | RemoteAgent):
        if isinstance(agent, RemoteAgent):
            await agent.fetch_info()
        assert isinstance(agent.name, str), "Agent name must be a string"
        agent_func_name = agent.name.replace(" ", "_").lower()
        func_name = f"transfer_to_{agent_func_name}"

        # Create transfer function using closure (no exec needed)
        # Note: No return type annotation to avoid Pydantic serialization issues
        def transfer_func(target_name: str = agent.name):
            """Transfer to the target agent."""
            return self.agents[target_name]

        transfer_func.__name__ = func_name
        transfer_func.__doc__ = f"Transfer to {agent.name}."
        await run_func(self.triage.tool, transfer_func)

        # Transfer back to triage
        # Note: No return type annotation to avoid Pydantic serialization issues
        def transfer_back_to_triage():
            """Transfer back to the triage agent."""
            return self.triage

        await run_func(agent.tool, transfer_back_to_triage)
        self.agents[agent.name] = agent

    async def remove_agent(self, agent: Agent | RemoteAgent):
        assert isinstance(agent.name, str), "Agent name must be a string"
        del self.agents[agent.name]
        self.triage.functions.pop(f"transfer_to_{agent.name.replace(' ', '_').lower()}")

    async def async_setup(self):
        while self._agents_to_add:
            agent = self._agents_to_add.pop(0)
            await self.add_agent(agent)

    async def run(self, msg: AgentInput, **kwargs):
        await self.async_setup()
        return await super().run(msg, **kwargs)


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
        # Call stack stores dicts with delegation context for proper return routing and message filtering
        # Each entry: {"caller_name": str, "instruction": str, "execution_context_id": str, "timestamp": float}
        self._call_stack: list[dict] = []

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
            calling_agent_name = calling_agent.name

            def call_agent(agent_name: str, instruction: str):
                """Delegate a task to a sub-agent in the team.

                Args:
                    agent_name: Name of the target sub-agent to delegate to
                    instruction: Clear task description for the target agent

                Returns:
                    Agent instance (triggers transfer and execution)
                """
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

                # Track the call in the call stack for proper return routing
                # Note: call_id will be set by the LLM tool calling mechanism (call["id"])
                # We store it when we receive the tool call result
                from uuid import uuid4

                self._call_stack.append(
                    {
                        "caller_name": calling_agent_name,
                        "instruction": instruction,
                        "tool_call_id": None,  # Will be set when tool call is processed
                        "execution_context_id": f"ctx_{str(uuid4())[:12]}",  # NEW: Context ID for message filtering
                        "timestamp": __import__("time").time(),
                    }
                )

                # Return the target agent instance (triggers transfer in team.run())
                return all_agents[agent_name]

            # Set proper function metadata for LLM
            call_agent.__name__ = "call_agent"
            call_agent.__doc__ = (
                "Delegate a task to a sub-agent in the team. "
                "Pass the agent_name and clear instruction describing what to do. "
                "The instruction will be passed to the sub-agent as context."
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
            # Pass execution_context_id from call stack if delegating via call_agent
            current_context_id = None
            if self._call_stack:
                current_context_id = self._call_stack[-1].get("execution_context_id")

            resp = await active_agent.run(
                msg, memory=memory, execution_context_id=current_context_id, **kwargs
            )
            if isinstance(resp, AgentTransfer):
                # Unified transfer handling in team.py
                # Two cases:
                # 1. call_agent delegation (has call_stack): will create tool_message after sub-agent completes
                # 2. transfer_to_* (no call_stack): create tool_message now and continue loop

                if self._call_stack:
                    # call_agent delegation case
                    delegation_context = self._call_stack[-1]
                    instruction = delegation_context["instruction"]
                    logger.info(
                        f"[CALL_AGENT] {active_agent.name} -> {resp.to_agent} | tool_call_id: {resp.tool_call_id}"
                    )
                    logger.info(
                        f"  instruction: {instruction[:100]}..."
                        if len(instruction) > 100
                        else f"  instruction: {instruction}"
                    )
                    # Attach context to the transfer - receiving agent knows what task and context
                    resp.instruction = instruction
                    resp.execution_context_id = delegation_context[
                        "execution_context_id"
                    ]
                    # Store the original call_id from the AgentTransfer
                    # This will be used when creating the tool_message after sub-agent completes
                    if resp.tool_call_id:
                        delegation_context["tool_call_id"] = resp.tool_call_id

                    self.set_active_agent(memory, resp.to_agent)
                    msg = resp
                else:
                    # transfer_to_* case: create tool_message to respond to the tool_call
                    logger.info(
                        f"[TRANSFER] {active_agent.name} -> {resp.to_agent} | tool_call_id: {resp.tool_call_id}"
                    )
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": resp.tool_call_id
                        or ("call_" + str(uuid.uuid4())[:20]),
                        "tool_name": "transfer",
                        "content": resp.to_agent,
                    }
                    # Switch to target agent and continue loop with tool_message
                    self.set_active_agent(memory, resp.to_agent)
                    msg = tool_message
            else:
                # Sub-agent completed and returned AgentResponse
                if self._call_stack:
                    delegation_context = self._call_stack.pop()
                    caller_name = delegation_context["caller_name"]
                    call_id = delegation_context.get("tool_call_id")
                    response_content = str(resp.content) if resp else ""

                    logger.info(
                        f"[CALL_AGENT_RESPONSE] {active_agent.name} -> {caller_name} | tool_call_id: {call_id}"
                    )
                    logger.info(
                        f"  response: {response_content[:100]}..."
                        if len(response_content) > 100
                        else f"  response: {response_content}"
                    )
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call_id or ("call_" + str(uuid.uuid4())[:20]),
                        "tool_name": "call_agent",
                        "content": response_content,
                    }

                    # Switch back to caller
                    self.set_active_agent(memory, caller_name)

                    msg = tool_message
                else:
                    return resp


class SequentialTeam(Team):
    """Team that run agents in sequential order."""

    def __init__(
        self,
        agents: list[Agent],
        connect_prompt: str | list[str] = "Next:",
    ):
        super().__init__(agents)
        self.order = list(self.agents.keys())
        self.connect_prompt = connect_prompt

    async def run(
        self,
        msg: AgentInput,
        connect_prompt: str | list[str] | None = None,
        agent_kwargs: dict = {},
        **final_kwargs,
    ):
        first = self.agents[self.order[0]]
        history = first.input_to_openai_messages(msg, False)
        for i, name in enumerate(self.order):
            kwargs = agent_kwargs.get(name, {})
            if i == len(self.order) - 1:
                kwargs.update(final_kwargs)
            resp = await self.agents[name].run(history, **kwargs)
            history.extend(resp.details.messages)
            # Inject the connect prompt between agents
            if i < len(self.order) - 1:
                c_prompt = connect_prompt or self.connect_prompt
                if isinstance(c_prompt, list):
                    c_prompt = c_prompt[i]
                history.append({"role": "user", "content": c_prompt})
        return resp


class MoATeam(Team):
    """Team that run agents in a MoA (Mixture-of-Agents) pattern.

    Reference:
        - [MoA: Mixure-of-Agents](https://arxiv.org/abs/2406.04692)
        - [Self-MoA](https://arxiv.org/abs/2502.00674)
    """

    AGGREGATION_TEMPLATE = """Below are responses from different AI models to the same query.  
Please carefully analyze these responses and generate a final answer that is:  
- Most accurate and comprehensive  
- Best aligned with the user's instructions  
- Free from errors or inconsistencies  

### Query:  
{user_query}  

### Responses:  
{responses}

### Final Answer:"""

    def __init__(
        self,
        proposers: list[Agent],
        aggregator: Agent,
        layers: int = 1,
        parallel: bool = True,
    ):
        super().__init__(proposers + [aggregator])
        self.proposers = proposers
        self.aggregator = aggregator
        self.layers = layers
        self.parallel = parallel

    def get_aggregate_prompt(
        self,
        user_query: list[dict],
        responses: dict[str, AgentResponse],
    ) -> str:
        resps_str = ""
        for i, resp in enumerate(responses.values()):
            resps_str += f"{i + 1}. {resp.agent_name}:\n{resp.content}\n\n"
        user_query_str = user_query[-1]["content"]
        return self.AGGREGATION_TEMPLATE.format(
            user_query=user_query_str,
            responses=resps_str,
        )

    async def run_proposers(
        self, input_, **proposer_kwargs
    ) -> dict[str, AgentResponse]:
        if self.parallel:
            tasks = [
                proposer.run(input_, **proposer_kwargs) for proposer in self.proposers
            ]
            gathered = await asyncio.gather(*tasks)
            return {
                proposer.name: resp for proposer, resp in zip(self.proposers, gathered)
            }
        else:
            responses = {}
            for proposer in self.proposers:
                resp = await proposer.run(input_, **proposer_kwargs)
                responses[proposer.name] = resp
            return responses

    async def run(
        self,
        msg: AgentInput,
        proposer_kwargs: dict = {},
        **aggregator_kwargs,
    ) -> AgentResponse:
        history = self.aggregator.input_to_openai_messages(msg)
        for i in range(self.layers):
            if i == 0:
                responses = await self.run_proposers(history, **proposer_kwargs)
            else:
                agg_prompt = self.get_aggregate_prompt(history, responses)
                responses = await self.run_proposers(agg_prompt, **proposer_kwargs)

        agg_prompt = self.get_aggregate_prompt(history, responses)
        resp = await self.aggregator.run(agg_prompt, **aggregator_kwargs)
        return resp
