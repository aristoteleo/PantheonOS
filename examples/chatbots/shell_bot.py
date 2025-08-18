import asyncio
from pantheon.agent import Agent
from pantheon.toolsets.shell import ShellToolSet
from pantheon.toolsets.utils.toolset import run_toolsets


async def main():
    toolset = ShellToolSet("shell")

    async with run_toolsets([toolset], log_level="WARNING"):
        agent = Agent(
            "shell_bot",
            "You are an AI assistant that can run shell commands.",
            model="gpt-4.1",
        )

        await agent.remote_toolset(toolset.service_id)
        await agent.chat()


if __name__ == "__main__":
    asyncio.run(main())
