import asyncio
from pantheon.agent import Agent
from pantheon.toolsets.web_browse.duckduckgo import duckduckgo_search
from pantheon.toolsets.web_browse.web_crawl import web_crawl


search_engine_expert = Agent(
    name="search_engine_expert",
    instructions="You are an expert in search engines. "
    "You can use the duckduckgo_search tool to search the web. "
    "You can also use the web_crawl tool to crawl the web.",
    model="gpt-4o-mini",
    tools=[duckduckgo_search, web_crawl],
)


async def main():
    await search_engine_expert.chat()


if __name__ == "__main__":
    asyncio.run(main())
