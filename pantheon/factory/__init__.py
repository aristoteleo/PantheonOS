import os

from ..agent import Agent
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
    for toolset in toolsets:
        try:
            s = await endpoint_service.invoke(
                "get_service", {"service_id_or_name": toolset}
            )
            if s is None:
                raise ValueError(f"{toolset} service not found")
            await agent.remote_toolset(s["id"])
        except Exception as e:
            logger.error(f"Failed to add toolset {toolset} to agent {name}: {e}")
            agent.not_loaded_toolsets.append(toolset)
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
