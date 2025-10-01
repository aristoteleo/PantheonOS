import asyncio
from pantheon.agent import Agent
from pantheon.toolsets.r import RInterpreterToolSet
from pantheon.toolsets.utils.toolset import run_toolsets
from pantheon.endpoint import ToolsetProxy


async def main():
    toolset = RInterpreterToolSet("r_interpreter")

    async with run_toolsets([toolset], log_level="WARNING"):
        agent = Agent(
            "r_bot",
            "You are an AI assistant that can run R code.",
            model="gpt-4o",
        )

        # Use ToolsetProxy instead of remote_toolset
        proxy = ToolsetProxy.from_toolset(toolset.service_id)
        await agent.toolset(proxy)
        await agent.chat()


if __name__ == "__main__":
    asyncio.run(main())
