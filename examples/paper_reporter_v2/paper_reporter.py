import asyncio
import os

from pantheon.task import Task, TasksSolver
from pantheon.agent import Agent
from pantheon.smart_func import smart_func
from pantheon.toolsets.web_browse import duckduckgo_search, web_crawl


def write_file(content: str, file_path: str):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


def read_directory(directory_path: str):
    return os.listdir(directory_path)


def read_file(file_path: str):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@smart_func(model="gpt-4o-mini")
async def extract_content(content: str) -> str:
    """Extract the most important content from the text.
    For example,
    if the text is a paper, extract the
    authors, title, journal, publication date,
    abstract, introduction, methods, results, and discussion.
    """


async def crawl_and_extract(urls: list[str]) -> list[str]:
    """Crawl provided urls and extract the most important content from each page.

    Args:
        urls: A list of urls to crawl.

    Returns:
        A list of contents extracted from the urls.
    """
    contents = await web_crawl(urls)
    extracted_contents = []
    for content in contents:
        try:
            extracted_content = await extract_content(content)
            extracted_contents.append(extracted_content)
        except Exception as e:
            print(str(e))
            extracted_contents.append("")
    return extracted_contents


async def main():
    theme = "The applications of LLM-based agents in biology and medicine."

    search_agent = Agent(
        name="Search Agent",
        instructions="""You are a search engine expert.""",
        model="gpt-4o",
        tools=[
            duckduckgo_search,
            crawl_and_extract,
            write_file,
            read_directory,
            read_file,
        ],
    )

    task = Task(
        "Collect papers",
        f"""Do the following steps:
1. Generate a list of keywords for the theme `{theme}` for searching papers.
2. Search for papers about the theme according to the keywords, find at least 30 papers.
3. Filter the search results, only keep the papers.
4. Crawl the url according to the filtered search results, extract the journal, authors, title, publication date, abstract, url, and other information.
5. Filter the contents according to the theme, and only keep the relevant papers.
6. Count the number of papers after filtering.
7. If the papers after filtering are not enough(less than 20), repeat the steps 1-6.
8. Write the results into a markdown file(keep item's url) to path `./report.md`. And check the file is written successfully. """,
    )

    tasks_solver = TasksSolver(task, search_agent)
    await tasks_solver.solve()


if __name__ == "__main__":
    asyncio.run(main())
