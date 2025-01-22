import warnings
warnings.filterwarnings("ignore")

import asyncio
import os

from pantheum.task import Task, TasksSolver
from pantheum.agent import Agent
from pantheum.tools.duckduckgo import duckduckgo_search
from pantheum.tools.web_crawl import web_crawl
from pantheum.smart_func import smart_func


def write_file(content: str, file_path: str):
    with open(file_path, "w") as f:
        f.write(content)
    return file_path


def read_directory(directory_path: str):
    return os.listdir(directory_path)


@smart_func
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
        extracted_content = await extract_content(content)
        extracted_contents.append(extracted_content)
    return extracted_contents


async def main():
    theme = "The applications of LLM-based agents in biology and medicine."

    search_agent = Agent(
        name="Search Agent",
        instructions = """You are a search engine expert.""",
        model="gpt-4o-mini",
        tools=[duckduckgo_search, crawl_and_extract, write_file, read_directory],
    )

    tasks = [
        Task("Keywords", f"Generate a list of keywords for the theme `{theme}` for searching papers. "),
        Task("Search", "Search for papers about the theme according to the keywords, find at least 30 papers."),
        Task("Crawl", "Crawl the papers according to the search results"),
        Task("Filter", "Filter the contents according to the theme, and only keep the relevant papers."),
        Task("Write", "Write the results into a markdown file, named with report.md"),
    ]

    tasks_solver = TasksSolver(tasks, search_agent)
    await tasks_solver.solve()


if __name__ == "__main__":
    asyncio.run(main())

