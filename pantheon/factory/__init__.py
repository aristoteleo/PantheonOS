import os

from ..agent import Agent
from ..endpoint import ToolsetProxy
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
    toolsets: list[str] | None = None,
    toolful: bool = False,
    **kwargs,
) -> Agent:
    """Create an agent from a template.

    Args:
        endpoint_service: The endpoint service to use for the agent.
        name: The name of the agent.
        instructions: The instructions for the agent.
        model: The model to use for the agent.
        icon: The icon to use for the agent.
        toolsets: The toolsets to use for the agent.
        toolful: Whether the agent is toolful.
    """
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        icon=icon,
    )
    agent.toolful = toolful
    agent.not_loaded_toolsets = []

    if toolsets is None:
        return agent

    logger.info(f"Agent '{name}': Adding {len(toolsets)} toolsets via ToolsetProxy")

    for toolset_name in toolsets:
        try:
            # Create ToolsetProxy (no connection needed, instant!)
            proxy = ToolsetProxy.from_endpoint(
                endpoint_service,
                toolset_name,
                cache_ttl=300,  # 5 minutes cache
                max_retries=3,
            )

            # Add toolset through proxy (lazy-loaded)
            await agent.toolset(proxy)

            logger.info(f"Agent '{name}': Registered toolset '{toolset_name}'")

        except Exception as e:
            logger.error(
                f"Agent '{name}': Failed to register toolset '{toolset_name}': {e}"
            )
            agent.not_loaded_toolsets.append(toolset_name)

    return agent


async def create_agents_from_template(endpoint_service, template: dict) -> dict:
    """Create agents from a template.

    Args:
        endpoint_service: The endpoint service to use for the agents.
        template: The template of the agents.

    Returns:
        A dictionary with the following keys:
        - triage: The triage agent.
        - other: The other agents.
    """
    agents = []
    triage_agent = None
    for name, agent_template in template.items():
        if name == "triage":
            triage_agent = await create_agent(endpoint_service, **agent_template)
        else:
            agents.append(await create_agent(endpoint_service, **agent_template))
    if triage_agent is None:
        raise ValueError("Triage agent not found")
    return {
        "triage": triage_agent,
        "other": agents,
    }
