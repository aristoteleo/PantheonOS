from pantheon.agent import Agent
from pantheon.endpoint import ToolsetProxy
from pantheon.utils.log import logger
from .template_manager import get_template_manager
from .models import TeamConfig, AgentConfig
from pantheon.settings import get_settings


async def create_agent(
    endpoint_service,
    name: str,
    instructions: str,
    model: str,
    icon: str,
    toolsets: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    description: str | None = None,
    **kwargs,
) -> Agent:
    """Create an agent from a template with all providers (toolsets and MCP servers).

    Args:
        endpoint_service: The endpoint service to use for the agent.
        name: The name of the agent.
        instructions: The instructions for the agent.
        model: The model to use for the agent.
        icon: The icon to use for the agent.
        toolsets: List of toolset names to add to the agent.
        mcp_servers: List of MCP server names to add to the agent.
        description: Optional description of the agent's purpose and capabilities.
    """
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        icon=icon,
        description=description,
    )
    agent.not_loaded_toolsets = []
    toolsets_added = []
    mcp_server_added = []
    toolsets = list(toolsets or [])
    mcp_servers = list(mcp_servers or [])
    # ===== Add ToolSet providers from config =====

    for toolset_name in toolsets:
        # Special handling: "task" toolset is local-only (not via Endpoint)
        if toolset_name == "task":
            try:
                from pantheon.toolsets.task import TaskToolSet

                task_toolset = TaskToolSet()
                await agent.toolset(task_toolset)
                toolsets_added.append(toolset_name)
                logger.debug(f"Agent '{name}': Added local TaskToolSet")
            except Exception as e:
                logger.error(f"Agent '{name}': Failed to add local TaskToolSet: {e}")
                agent.not_loaded_toolsets.append(toolset_name)
            continue

        try:
            # Create ToolsetProxy for remote toolsets
            proxy = ToolsetProxy.from_endpoint(endpoint_service, toolset_name)

            from pantheon.providers import ToolSetProvider

            toolset_provider = ToolSetProvider(proxy)
            await toolset_provider.initialize()

            # Add provider to agent
            await agent.toolset(toolset_provider)
            toolsets_added.append(toolset_name)

        except Exception as e:
            logger.error(f"Agent '{name}': Failed to add toolset '{toolset_name}': {e}")
            agent.not_loaded_toolsets.append(toolset_name)

    # ===== Add MCP provider from unified gateway =====
    # All MCP servers are accessible via the unified gateway at /mcp
    # with prefixed tool names (e.g., context7_resolve_library_id)

    if get_settings().enable_mcp_tools:
        try:
            from pantheon.utils.misc import call_endpoint_method
            from pantheon.providers import MCPProvider

            # Get unified gateway URI (special name="mcp" returns gateway info)
            result = await call_endpoint_method(
                endpoint_service,
                endpoint_method_name="manage_service",
                action="get",
                service_type="mcp",
                name="mcp",  # Special: returns unified gateway URI
            )

            if not result.get("success"):
                raise UserWarning(
                    f"Failed to get unified gateway: {result.get('message', 'Unknown error')}"
                )

            unified_uri = result.get("service", {}).get("uri")
            if not unified_uri:
                raise UserWarning("Unified gateway has no URI configured")

            # Use singleton MCPProvider for the unified gateway
            mcp_provider = MCPProvider.get_instance(unified_uri)
            await mcp_provider.initialize()

            # Add as single "mcp" provider (all tools accessible via prefix)
            await agent.mcp("mcp", mcp_provider)
            mcp_server_added.append("mcp")
            logger.info(
                f"Agent '{name}': Connected to unified MCP gateway at {unified_uri}"
            )

        except UserWarning as e:
            logger.warning(f"Agent '{name}': {e}")
        except Exception as e:
            logger.error(f"Agent '{name}': Failed to add unified MCP provider: {e}")


    logger.info(
        f"Agent {name} added toolsets: {toolsets_added} mcp_servers: {mcp_server_added}"
    )
    return agent


async def create_agents_from_template(endpoint_service, agent_configs: dict) -> list:
    """Create agents from agent configs."""
    agents = []

    for agent_config in agent_configs.values():
        agent = await create_agent(endpoint_service, **agent_config)
        agents.append(agent)

    return agents
