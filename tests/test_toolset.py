from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet


async def test_agent_call_local_toolset():
    toolset = PythonInterpreterToolSet("python_interpreter")
    agent = Agent(
        "test",
        "You are an asistant, help me test my code",
    )
    agent.toolset(toolset)

    resp = await agent.run("Run the code `print('Hello, world!')`")
    print(resp.content)
    assert "Hello, world!" in resp.content
