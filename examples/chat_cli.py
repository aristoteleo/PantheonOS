import warnings
warnings.filterwarnings("ignore")

import asyncio
from pantheum.repl.meeting import Repl
from pantheum.agent import Agent
from pantheum.tools.duckduckgo import duckduckgo_search
from pantheum.tools.web_crawl import web_crawl


scifi_fan = Agent(
    name="scifi_fan",
    instructions="You are a scifi fan. Please disguise yourself as a human and talk to me.",
    model="gpt-4o-mini",
)


search_engine_expert = Agent(
    name="search_engine_expert",
    instructions="You are an expert in search engines. " \
        "You can use the duckduckgo_search tool to search the web. " \
        "You can also use the web_crawl tool to crawl the web.",
    model="gpt-4o-mini",
    tools=[duckduckgo_search, web_crawl],
)


async def main():
    repl = Repl([scifi_fan, search_engine_expert])
    await repl.run()


if __name__ == "__main__":
    asyncio.run(main())
