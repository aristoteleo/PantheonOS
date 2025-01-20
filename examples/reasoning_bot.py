import warnings
warnings.filterwarnings("ignore")

import asyncio
from pantheum.agent import Agent
from pantheum.reasoning import reasoning_flash_thinking_2


reasoning_bot = Agent(
    name="reasoning_bot",
    instructions="You are an AI assistant with reasoning abilities. " \
        "You can use reasoning to solve complex problems.",
    model="gpt-4o-mini",
    tools=[reasoning_flash_thinking_2],
)


async def main():
    await reasoning_bot.chat()


if __name__ == "__main__":
    asyncio.run(main())
