from ..agent import Agent


async def default_agents_factory(endpoint) -> dict:
    assistant_agent = Agent(
        name="Assistant",
        instructions="You are a helpful assistant that can answer questions and help with tasks.",
        model="gpt-4.1",
        icon="🤖",
    )
    s = await endpoint.invoke("get_service", {"service_id_or_name": "python_interpreter"})
    if s is None:
        raise ValueError("Python interpreter service not found")
    await assistant_agent.remote_toolset(s["id"])

    s = await endpoint.invoke("get_service", {"service_id_or_name": "file_manager"})
    if s is None:
        raise ValueError("File manager service not found")
    await assistant_agent.remote_toolset(s["id"])

    web_search_agent = Agent(
        name="Web search",
        instructions="You are a web search agent that can search the web for information.",
        model="gpt-4.1",
        icon="🔍",
    )

    s = await endpoint.invoke("get_service", {"service_id_or_name": "web_browse"})
    if s is None:
        raise ValueError("Web browser service not found")
    await web_search_agent.remote_toolset(s["id"])

    return {
        "triage": assistant_agent,
        "other": [web_search_agent],
    }