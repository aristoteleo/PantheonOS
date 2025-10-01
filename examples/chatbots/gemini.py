import asyncio
from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.utils.toolset import run_toolsets
from pantheon.endpoint import ToolsetProxy


async def main():
    toolset = PythonInterpreterToolSet("python_interpreter")
    async with run_toolsets([toolset], log_level="WARNING"):
        agent = Agent(
            "coderun_bot",
            "You are an AI assistant that can run Python code.",
            model="gemini/gemini-2.0-flash",
        )
        # Use ToolsetProxy instead of remote_toolset
        proxy = ToolsetProxy.from_toolset(toolset.service_id)
        await agent.toolset(proxy)
        await agent.chat()


if __name__ == "__main__":
    asyncio.run(main())
