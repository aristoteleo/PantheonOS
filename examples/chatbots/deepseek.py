import asyncio
from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.utils.toolset import run_toolsets


async def main():
    toolset = PythonInterpreterToolSet("python_interpreter")
    async with run_toolsets([toolset], log_level="WARNING"):
        agent = Agent(
            "coderun_bot",
            "You are an AI assistant that can run Python code.",
            model="deepseek/deepseek-chat",
        )
        await agent.remote_toolset(toolset.service_id)
        await agent.chat()


if __name__ == "__main__":
    asyncio.run(main())
