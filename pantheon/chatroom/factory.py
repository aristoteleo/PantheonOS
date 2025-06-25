import os

from ..agent import Agent
from ..utils.log import logger


DEFAULT_AGENTS_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "default_agents_templates.yaml")


async def create_agent(
        endpoint,
        name: str,
        instructions: str,
        model: str,
        icon: str,
        toolsets: list[str],
) -> Agent:
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        icon=icon,
    )
    for toolset in toolsets:
        try:
            s = await endpoint.invoke("get_service", {"service_id_or_name": toolset})
            if s is None:
                raise ValueError(f"{toolset} service not found")
            await agent.remote_toolset(s["id"])
        except Exception as e:
            logger.error(f"Failed to add toolset {toolset} to agent {name}: {e}")
    return agent


async def create_agents_from_template(endpoint, template: dict) -> dict:
    agents = []
    triage_agent = None
    for name, agent_template in template.items():
        if name == "triage":
            triage_agent = await create_agent(endpoint, **agent_template)
        else:
            agents.append(await create_agent(endpoint, **agent_template))
    if triage_agent is None:
        raise ValueError("Triage agent not found")
    return {
        "triage": triage_agent,
        "other": agents,
    }
