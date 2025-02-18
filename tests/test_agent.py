from pathlib import Path
import asyncio

from pantheon.agent import Agent, AgentTransfer
from pantheon.utils.vision import vision_input
from pydantic import BaseModel, Field
from typing import List
import random


HERE = Path(__file__).parent


async def test_stream():
    agent = Agent(
        name="test",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions. Just give me a number without any other words and symbols.",
        model="gpt-4o-mini",
    )
    msgs = [{"role": "user", "content": "What is the meaning of life?"}]
    resp = await agent.run_stream(
        msgs,
        process_chunk=lambda chunk: print(chunk.get("content", ""), end="", flush=True),
    )
    print("\n", resp)
    assert len(resp.messages) == 1


async def test_tool_use():
    agent = Agent(
        name="test",
        instructions="You are a weather agent, you can use the weather API to get the weather of a city.",
        model="gpt-4o-mini",
    )

    @agent.tool
    def get_weather(city: str, unit: str = "celsius"):
        """Get the weather of a city."""
        return {"weather": "sunny", "temperature": 20}

    msgs = [{"role": "user", "content": "What is the weather in Palo Alto and Redwood City?"}]
    resp = await agent.run_stream(msgs)
    print()
    print(resp)
    call_id = resp.messages[0]["tool_calls"][0]["id"]
    assert resp.context_variables[call_id]["weather"] == "sunny"


async def test_structured_output():
    agent = Agent(
        name="test",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions.",
        model="gpt-4o-mini",
    )

    class SciFiBook(BaseModel):
        title: str
        author: str
        year: int

    class SciFiBookList(BaseModel):
        books: List[SciFiBook]

    def assert_chunk(chunk):
        assert isinstance(chunk, dict)

    resp = await agent.run(
        [{"role": "user", "content": "Recommend me 5 sci-fi books."}],
        response_format=SciFiBookList,
        process_chunk=assert_chunk,
    )

    print(resp)
    assert isinstance(resp.details.messages[0]["parsed"], SciFiBookList)
    assert len(resp.details.messages[0]["parsed"].books) == 5

    resp = await agent.run(
        [{"role": "user", "content": "1 + 1 = 2?"}],
        response_format=bool,
    )
    assert resp.content is True


async def test_structured_output_with_tool_use():
    agent = Agent(
        name="test",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions, and you can use the tool calls to get the rating of each book.",
        model="gpt-4o-mini",
    )

    class SciFiBook(BaseModel):
        title: str
        author: str
        year: int

    class SciFiBookList(BaseModel):
        books: List[SciFiBook]
        ratings: List[float] = Field(description="Use function call to get the rating of each book.")

    called = False

    @agent.tool
    def get_book_rating(title: str) -> float:
        """Get the rating of a book."""
        nonlocal called
        called = True
        return random.random()

    answer = await agent.run(
        "Recommend me 5 sci-fi books, and use `get_book_rating` function to get the rating of each book.",
        response_format=SciFiBookList,
    )
    assert called
    assert isinstance(answer.content, SciFiBookList)
    assert len(answer.content.books) == 5
    for r in answer.content.ratings:
        assert 0 <= r <= 1.0


async def test_agent_result_passing():
    scifi_fan = Agent(
        name="scifi_fan",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions.",
        model="gpt-4o-mini",
    )

    scifi_critic = Agent(
        name="scifi_critic",
        instructions="You are a scifi critic, you can review a sci-fi book and give a rating.",
        model="gpt-4o-mini",
    )

    summary_agent = Agent(
        name="summary_agent",
        instructions="You are a summary agent, you can summarize the answer of other agent give a human readable format.",
        model="gpt-4o-mini",
    )

    class SciFiBook(BaseModel):
        title: str
        author: str
        year: int

    class SciFiBookList(BaseModel):
        books: List[SciFiBook]

    answer_recommend = await scifi_fan.run(
        "Recommend me 5 sci-fi books.",
        response_format=SciFiBookList,
    )
    print(answer_recommend.content)

    class SciFiBookListWithRating(SciFiBookList):
        ratings: List[float] = Field(description="Rate the book from 0 to 10.")
        comments: List[str] = Field(description="Comment on the book.")

    answer_critic = await scifi_critic.run(
        answer_recommend,
        response_format=SciFiBookListWithRating,
    )
    print(answer_critic.content)

    assert isinstance(answer_critic.content, SciFiBookListWithRating)
    assert len(answer_critic.content.books) == 5
    assert len(answer_critic.content.ratings) == 5
    assert len(answer_critic.content.comments) == 5

    answer_summary = await summary_agent.run(
        answer_critic,
    )
    print(answer_summary.content)


async def test_tool_timeout():
    agent = Agent(
        name="test",
        instructions="",
        model="gpt-4o-mini",
        tool_timeout=1,
        use_memory=False,
    )

    @agent.tool
    def get_weather(city: str, unit: str = "celsius"):
        """Get the weather of a city."""
        return {"weather": "sunny", "temperature": 20}

    resp = await agent.run("What is the weather in Palo Alto?")
    print(resp.content)

    agent.functions.clear()

    flag = True

    @agent.tool
    async def get_weather(city: str, unit: str = "celsius"):
        """Get the weather of a city."""
        await asyncio.sleep(2)
        nonlocal flag
        flag = False

    resp = await agent.run("What is the weather in Palo Alto?")
    assert flag
    print(resp)


async def test_agent_transfer():
    scifi_fan = Agent(
        name="scifi_fan",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions.",
        model="gpt-4o-mini",
    )

    classic_literature_fan = Agent(
        name="classic_literature_fan",
        instructions="You are a classic literature fan, you can answer any classic literature related questions.",
        model="gpt-4o-mini",
    )


    @scifi_fan.tool
    def transfer_to_classic_literature_fan():
        """Transfer the question to the classic literature fan."""
        return classic_literature_fan

    resp = await scifi_fan.run("What is the best classic literature book?")
    assert isinstance(resp, AgentTransfer)
    assert resp.from_agent == scifi_fan.name
    assert resp.to_agent == classic_literature_fan.name


async def test_agent_force_litellm():
    agent = Agent(
        name="test",
        instructions="",
        model="gpt-4o-mini",
        force_litellm=True,
    )

    resp = await agent.run("What is the weather in Palo Alto?")
    print(resp.content)

    class Book(BaseModel):
        title: str
        author: str

    resp = await agent.run("Recommend me 5 sci-fi books.", response_format=list[Book])
    print(resp.content)


async def test_vision():
    agent = Agent(
        name="test",
        instructions="You are a vision agent, you can answer any vision related questions.",
        model="gpt-4o-mini",
    )

    resp = await agent.run(
        vision_input("Is there a dog in the image?", HERE / "data/animal.png", from_path=True),
        response_format=bool,
    )
    print(resp.content)
    assert resp.content is True

