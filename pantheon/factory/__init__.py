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
    enable_mcp: bool = True,
    think_tool: bool = False,
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
            "think" is a reserved name that enables the think tool.
        mcp_servers: List of MCP server names to add to the agent.
        description: Optional description of the agent's purpose and capabilities.
        think_tool: Whether to enable the think tool (deprecated, use toolsets=["think"] instead).
    """
    toolsets = list(toolsets or [])

    # Extract "think" from toolsets — it's a built-in tool, not a remote toolset
    if "think" in toolsets:
        think_tool = True
        toolsets = [t for t in toolsets if t != "think"]

    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        icon=icon,
        description=description,
        think_tool=think_tool,
    )
    agent.not_loaded_toolsets = []
    toolsets_added = []
    mcp_server_added = []
    mcp_servers = list(mcp_servers or [])
    
    # ===== Parse toolsets to extract MCP specs =====
    normal_toolsets = []
    mcp_from_toolsets = []
    
    for spec in toolsets:
        if spec == "mcp":
            mcp_from_toolsets.append("mcp")  # Explicit "mcp" request
        elif spec.startswith("mcp:"):
            mcp_from_toolsets.append(spec[4:])  # Extract: "mcp:context7" -> "context7"
        else:
            normal_toolsets.append(spec)
    
    # Merge all MCP sources into one set
    all_mcp_servers = set(mcp_servers + mcp_from_toolsets)
    
    # If enable_mcp=True, add "mcp" to the set (unified gateway)
    if enable_mcp and get_settings().enable_mcp_tools:
        all_mcp_servers.add("mcp")
    
    # Save specific servers before deduplication (for startup)
    servers_to_start = [s for s in all_mcp_servers if s != "mcp"]
    
    # Optimization: If "mcp" (unified gateway) is present, remove specific servers
    # because "mcp" already includes all MCP tools (would cause duplicates)
    if "mcp" in all_mcp_servers:
        specific_servers = all_mcp_servers - {"mcp"}
        if specific_servers:
            logger.info(
                f"Agent '{name}': Unified MCP gateway includes all tools. "
                f"Skipping specific servers {list(specific_servers)} to avoid duplicates."
            )
            all_mcp_servers = {"mcp"}
    
    # ===== Add ToolSet providers from config =====

    for toolset_name in normal_toolsets:
        # "task" toolset is now managed by TaskSystemPlugin via plugin registry
        if toolset_name == "task":
            logger.debug(f"Agent '{name}': 'task' toolset is managed by TaskSystemPlugin, skipping")
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

    # ===== Add MCP providers =====
    # Loop handles empty set naturally - no execution if set is empty
    
    # First, ensure all required MCP servers are started
    if servers_to_start:
        try:
            from pantheon.utils.misc import call_endpoint_method
            
            logger.info(f"Agent '{name}': Ensuring MCP servers are started: {servers_to_start}")
            result = await call_endpoint_method(
                endpoint_service,
                endpoint_method_name="manage_service",
                action="start",
                service_type="mcp",
                name=servers_to_start,
            )
            if not result.get("success"):
                logger.warning(
                    f"Agent '{name}': Failed to start some MCP servers: {result.get('errors', [])}"
                )
            else:
                logger.info(
                    f"Agent '{name}': MCP servers ready: {result.get('started', [])}"
                )
        except Exception as e:
            logger.warning(f"Agent '{name}': Error ensuring MCP servers: {e}")
    
    # Now add MCP providers
    try:
        from pantheon.utils.misc import call_endpoint_method
        from pantheon.providers import MCPProvider

        # Get unified gateway URI (only if we'll use it)
        unified_uri = None
        for server_name in all_mcp_servers:
            # Get URI on first iteration
            if unified_uri is None:
                result = await call_endpoint_method(
                    endpoint_service,
                    endpoint_method_name="manage_service",
                    action="get",
                    service_type="mcp",
                    name="mcp",
                )

                if not result.get("success"):
                    raise UserWarning(
                        f"Failed to get unified gateway: {result.get('message', 'Unknown error')}"
                    )

                unified_uri = result.get("service", {}).get("uri")
                if not unified_uri:
                    raise UserWarning("Unified gateway has no URI configured")

            # Add provider for this MCP server
            if server_name == "mcp":
                # Unified gateway - no filtering
                provider = MCPProvider.get_instance(unified_uri)
            else:
                # Specific server - filter by prefix
                provider = MCPProvider.get_instance(unified_uri, filter_prefix=server_name)

            await provider.initialize()
            await agent.mcp(server_name, provider)
            mcp_server_added.append(server_name)

    except UserWarning as e:
        logger.warning(f"Agent '{name}': {e}")
    except Exception as e:
        logger.error(f"Agent '{name}': Failed to add MCP provider: {e}")

    logger.info(
        f"Agent {name} added toolsets: {toolsets_added} mcp_servers: {mcp_server_added}"
    )
    return agent


async def create_agents_from_template(
    endpoint_service, agent_configs: dict, enable_mcp: bool = True
) -> list:
    """Create agents from agent configs."""
    agents = []

    for agent_config in agent_configs.values():
        agent = await create_agent(
            endpoint_service, enable_mcp=enable_mcp, **agent_config
        )
        agents.append(agent)

    return agents


async def create_team_from_template(
    endpoint_service,
    template_id: str,
    check_toolsets: bool = True,
    enable_mcp: bool = True,
):
    """Create a PantheonTeam from a template.

    This is the primary factory function for creating teams, suitable for:
    - Benchmark testing
    - Programmatic team creation

    Workflow:
    1. Loads the team template by ID
    2. Prepares agent configurations
    3. Optionally checks toolset availability
    4. Creates agents with endpoint connection
    5. Initializes plugins (memory, learning, compression)
    6. Returns a fully initialized PantheonTeam

    Args:
        endpoint_service: The endpoint service for toolset/MCP connections
        template_id: Team template ID (e.g., "default", "data_research_team")
        check_toolsets: If True, warns about unavailable toolsets
        enable_mcp: If True, enables MCP server connections

    Returns:
        PantheonTeam: Fully initialized team ready to run

    Raises:
        ValueError: If template not found
    """
    from pantheon.team import PantheonTeam
    from pantheon.utils.misc import call_endpoint_method


    # 2. Load template
    template_manager = get_template_manager()
    team_config = template_manager.get_template(template_id)
    if not team_config:
        raise ValueError(f"Team template '{template_id}' not found")

    # 3. Prepare team agents
    (
        agent_configs,
        required_toolsets,
        required_mcp_servers,
    ) = template_manager.prepare_team(team_config)

    # 4. Log required toolsets (auto-start handles missing ones on first use)
    if check_toolsets and required_toolsets:
        logger.debug(f"Team '{template_id}' requires toolsets: {required_toolsets}")

    # 5. Create agents
    agents = await create_agents_from_template(
        endpoint_service, agent_configs, enable_mcp=enable_mcp
    )

    # 6. Initialize plugins via centralized registry
    from pantheon.team.plugin_registry import create_plugins
    plugins = create_plugins(get_settings())

    # 7. Create and setup team with plugins
    team = PantheonTeam(
        agents=agents,
        plugins=plugins,
    )
    await team.async_setup()

    logger.info(f"Team '{template_id}' created with {len(agents)} agents and {len(plugins)} plugins")
    return team
