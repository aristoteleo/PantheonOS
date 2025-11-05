import os

from ..agent import Agent
from ..endpoint import ToolsetProxy
from ..endpoint.mcp import MCPServerConfig
from ..providers import MCPProvider, ToolSetProvider
from ..utils.log import logger


DEFAULT_AGENTS_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "default_agents_templates.yaml"
)


async def create_agent(
    endpoint_service,
    name: str,
    instructions: str,
    model: str,
    icon: str,
    toolsets: list[str] = [],
    mcp_servers: list[str] = [],
    toolful: bool = False,
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
        toolful: Whether the agent is toolful.
        description: Optional description of the agent's purpose and capabilities.
    """
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        icon=icon,
        description=description,
    )
    agent.toolful = toolful
    agent.not_loaded_toolsets = []
    toolsets_added = []
    mcp_server_added = []
    # ===== Add ToolSet providers from config =====

    for toolset_name in toolsets:
        try:
            # Create ToolsetProxy
            proxy = ToolsetProxy.from_endpoint(endpoint_service, toolset_name)

            toolset_provider = ToolSetProvider(proxy)
            await toolset_provider.initialize()

            # Add provider to agent
            await agent.toolset(toolset_provider)
            toolsets_added.append(toolset_name)

        except Exception as e:
            logger.error(f"Agent '{name}': Failed to add toolset '{toolset_name}': {e}")
            agent.not_loaded_toolsets.append(toolset_name)

    # ===== Add MCP providers from config =====

    for mcp_name in mcp_servers:
        try:
            # Get MCP server config from endpoint
            # endpoint_service can be either Endpoint instance or remote service
            from ..utils.misc import call_endpoint_method

            service_result = await call_endpoint_method(
                endpoint_service,
                endpoint_method_name="manage_service",
                action="get",
                service_type="mcp",
                name=mcp_name,
            )

            if not service_result.get("success"):
                logger.warning(
                    f"Agent '{name}': Failed to get MCP config for '{mcp_name}': "
                    f"{service_result.get('message', 'Unknown error')}"
                )
                continue

            # Extract service details
            service_info = service_result.get("service", {})
            mcp_uri = service_info.get("uri")

            if not mcp_uri:
                logger.warning(
                    f"Agent '{name}': MCP server '{mcp_name}' has no URI configured"
                )
                continue

            # Create MCPServerConfig with minimal info (URI from running service)
            mcp_config = MCPServerConfig(
                name=mcp_name,
                type="http",
                uri=mcp_uri,
                description=service_info.get("description", ""),
            )

            # Create and initialize MCPProvider with agent's model for sampling
            mcp_provider = MCPProvider(
                mcp_config,
                model=model,  # Use agent's model for sampling requests
            )
            await mcp_provider.initialize()

            # Add to agent
            await agent.mcp(mcp_name, mcp_provider)
            mcp_server_added.append(mcp_name)

        except Exception as e:
            agent.not_loaded_toolsets.append(mcp_name)
            logger.error(
                f"Agent '{name}': Failed to add MCP provider '{mcp_name}': {e}"
            )

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
