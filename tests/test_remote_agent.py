import asyncio

from pantheon.agent import Agent, AgentResponse, AgentService, RemoteAgent


async def test_remote_agent():
    agent = Agent(
        "scifi_fan",
        "You are a scifi fan.",
    )
    service = AgentService(agent)
    service_task = asyncio.create_task(service.run())
    await asyncio.sleep(1.0)
    # Access worker after it's initialized by run()
    remote_agent = RemoteAgent(service.worker.service_id)
    res = await remote_agent.run("What is the best scifi book?")
    assert isinstance(res, AgentResponse)
    service_task.cancel()


# FIX: currently NATs backend doesn't support reverse callable
async def test_remote_agent_print_chunk():
    agent = Agent(
        "scifi_fan",
        "You are a scifi fan.",
    )
    service = AgentService(agent)
    service_task = asyncio.create_task(service.run())
    await asyncio.sleep(1.0)
    # Access worker after it's initialized by run()
    remote_agent = RemoteAgent(service.worker.service_id)
    _flag = False

    def print_chunk(chunk):
        nonlocal _flag
        _flag = True
        print(chunk["content"], end="", flush=True)

    res = await remote_agent.run(
        "What is the best scifi book?",
        process_chunk=print_chunk,
    )
    assert isinstance(res, AgentResponse)
    service_task.cancel()
    assert _flag
    assert remote_agent.name == "scifi_fan"


# FIX: not working now
async def test_remote_agent_tool():
    agent = Agent(
        "assistant",
        "You are a assistant, you can use tools to get information.",
    )
    service = AgentService(agent)
    service_task = asyncio.create_task(service.run())
    await asyncio.sleep(1.0)
    # Access worker after it's initialized by run()
    remote_agent = RemoteAgent(service.worker.service_id)

    def fetch_weather(city: str):
        print(f"fetching weather in {city}")
        return f"The weather in {city} is sunny"

    await remote_agent.tool(fetch_weather)

    def print_step_message(message):
        print(message)

    res = await remote_agent.run(
        "What is the weather in Tokyo?",
        process_step_message=print_step_message,
    )
    assert isinstance(res, AgentResponse)
    service_task.cancel()
