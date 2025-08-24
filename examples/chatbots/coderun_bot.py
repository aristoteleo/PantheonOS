import asyncio
from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet


async def main():
    toolset = PythonInterpreterToolSet("python_interpreter")
    agent = Agent(
        "coderun_bot",
        "You are an AI assistant that can run Python code.",
        model="gpt-5",
    )
    agent.toolset(toolset)
    await agent.chat()


if __name__ == "__main__":
    asyncio.run(main())
