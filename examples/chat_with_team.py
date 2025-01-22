import asyncio

from pantheum.agent import Agent
from pantheum.repl.meeting import Repl
from pantheum.meeting import TeamMeeting
from pantheum.tools.duckduckgo import duckduckgo_search


search_engine_expert = Agent(
    name="search_engine_expert",
    instructions="You are a search engine expert, you will search the internet for information.",
    model="gpt-4o-mini",
    tools=[duckduckgo_search],
)


pi_agent = Agent(
    name="principal_investigator",
    instructions="You will consider the user's question as the principal investigator of the project. You should assign tasks to your team members.",
    model="gpt-4o",
)


meeting = TeamMeeting(pi_agent, [search_engine_expert])
repl = Repl(meeting)

asyncio.run(repl.run())
