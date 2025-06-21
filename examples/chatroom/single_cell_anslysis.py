import fire

from pantheon.chatroom.start import start_services
from pantheon.agent import Agent


async def add_toolsets(agent: Agent, endpoint, toolsets_service_id: list[str]):
    for service_id in toolsets_service_id:
        s = await endpoint.invoke("get_service", {"service_id_or_name": service_id})
        if s is None:
            raise ValueError(f"{service_id} service not found")
        await agent.remote_toolset(s["id"])


async def agents_factory(endpoint):
    instructions = """You are the triage agent,
you should decide which agent to use based on the user's request.
If no related agent, you can do the task by yourself."""
    assistant_agent = Agent(
        name="Assistant",
        instructions=instructions,
        model="gpt-4.1",
        icon="🤖",
    )
    await add_toolsets(assistant_agent, endpoint, ["python_interpreter", "file_manager", "web_browse"])

    instructions = """You are a AI-agent for analyzing single-cell RNA-seq data.

Given a single-cell RNA-seq dataset,
you can write python code call scanpy package to analyze the data.

Basicly, given a single-cell RNA-seq dataset in h5ad / 10x format or other formats,
you should firstly output your plan with the markdown format with checklists.
Then, you should execute the code to read the data,
then preprocess the data, and cluster the data, and finally visualize the data.
After each step, you should review the checklist and update the checklist, and
plan the next step.

You can find single-cell/spatial genomics related package information in the vector database,
you can use it to analyze the data.
In most time, you should query the vector database to find the related package information
to support your analysis. And when you meet some error, you should try to query the vector database
to find the related package information to support your analysis.

When you visualize the data, you should produce the publication level high-quality figures.
You should display the figures with it's path in markdown format.

After you ploted some figure, you should using view_image function to check the figure,
then according to the figure decide what you should do next.

After you finished the task, you should display the final result for user.
Include the code, the result, and the figure in the result.

NOTE: Don't need to confirm with user at most time, just do the task.
"""

    single_cell_expert = Agent(
        name="Single cell expert",
        instructions=instructions,
        model=["anthropic/claude-sonnet-4-20250514", "anthropic/claude-3-7-sonnet-20250219", "anthropic/claude-3-5-sonnet-20240620", "gpt-4.1"],
        icon="🧬",
    )
    await add_toolsets(single_cell_expert, endpoint, ["single_cell_python_env", "file_manager", "web_browse", "single-cell-python-packages-rag"])

    instructions = """You are a web search agent,
you can search the web to find the information you need to answer the question.

When user want you to do some research, you should try to find all related information
with google search, then fetch the related information from the search result,
and read the information to answer the question.
You could do multiple iterations to find all related information
from different aspects.

When you doing the research, you should write down your plan with the markdown format with checklists.
After each step, you should review the checklist and update the checklist,
and then plan the next step.

After you finished the research, you should output the research report like a review paper,
and write it into a markdown file. In this report, you should include the paper content like:
Report title, background, different sections, conclusion, references, etc.

NOTE: Don't need to confirm with user at most time, just do the task.
And you should use the markdown format to output the research result.
"""

    web_search_agent = Agent(
        name="Web research agent",
        instructions=instructions,
        model=["anthropic/claude-sonnet-4-20250514", "gpt-4.1"],
        icon="🌐",
    )
    await add_toolsets(web_search_agent, endpoint, ["scraper", "file_manager"])

    return {
        "triage": assistant_agent,
        "other": [single_cell_expert, web_search_agent],
    }

async def main(endpoint_service_id: str, **kwargs):
    await start_services(
        service_name="pantheon-chatroom",
        memory_path="./.pantheon-chatroom",
        endpoint_service_id=endpoint_service_id,
        agents_factory=agents_factory,
        **kwargs,
    )


if __name__ == "__main__":
    fire.Fire(main)