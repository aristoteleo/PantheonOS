import asyncio
from pantheon.agent import Agent
from pantheon.tools.r import RInterpreterToolSet
from pantheon.remote import run_toolsets


async def main():
    toolset = RInterpreterToolSet("r_interpreter")

    async with run_toolsets([toolset], log_level="WARNING"):
        agent = Agent(
            "r_bot",
            "You are an AI assistant that can run R code.",
            model="gpt-4o",
        )

        await agent.remote_toolset(toolset.service_id)
        await agent.chat()


if __name__ == "__main__":
    asyncio.run(main())
