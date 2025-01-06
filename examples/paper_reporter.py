"""
This is an example of using synago to build a multi-agent system to generate a markdown report of papers about a theme.

# Install the dependencies
```bash
pip install synago[tool]
python -m playwright install --with-deps chromium
```

# Run the program
```bash
python examples/paper_reporter.py --theme "The applications of LLM-based agents in biology and medicine." --output paper_report.md --results_per_keyword 5
```
"""
from pprint import pprint
import asyncio

import fire
from pantheum.agent import Agent
from pantheum.tools.duckduckgo import duckduckgo_search
from pantheum.tools.web_crawl import web_crawl
from loguru import logger
from pydantic import BaseModel, Field


default_theme = "The applications of LLM-based agents in biology and medicine."


async def main(
    theme: str = default_theme,
    output: str | None = None,
    results_per_keyword: int = 5,
):
    """This program will generate a markdown report of papers about the theme.

    The program will first query the keywords about the theme,
    then it will crawl the web to get the contents of the papers,
    and finally it will extract the information of the papers and format them into a markdown report.


    Args:
        theme (str): The theme of the paper report.
        output (str | None): The path to save the markdown report.
        results_per_keyword (int): The number of results per keyword.
    """

    query_keywords_agent = Agent(
        name="query_keywords_agent",
        instructions="""You are a search engine expert,
    you can generate a list of query keywords for a search engine to find the most relevant papers.

    ## Duckduckgo query operators

    | Keywords example |	Result|
    | ---     | ---   |
    | cats dogs |	Results about cats or dogs |
    | "cats and dogs" |	Results for exact term "cats and dogs". If no results are found, related results are shown. |
    | cats -dogs |	Fewer dogs in results |
    | cats +dogs |	More dogs in results |
    | cats filetype:pdf |	PDFs about cats. Supported file types: pdf, doc(x), xls(x), ppt(x), html |
    | dogs site:example.com  |	Pages about dogs from example.com |
    | cats -site:example.com |	Pages about cats, excluding example.com |
    | intitle:dogs |	Page title includes the word "dogs" |
    | inurl:cats  |	Page url includes the word "cats" |
    """,
        model="gpt-4o-mini",
    )

    def merge_search_results(results: list[dict]) -> list[dict]:
        _dict = {}
        for result in results:
            _dict[result["title"]] = result
        return list(_dict.values())

    info_extraction_agent = Agent(
        name="info_extraction_agent",
        instructions=f"""You are a expert in the theme: `{theme}`,
    you should extract the paper title, summary, journal, time from the page content.
    You should also check if the search result is a paper and related to the theme.

    Please be very strict and careful,
    only return True if the paper is very related to the theme.
    """,
        model="gpt-4o-mini",
    )

    format_agent = Agent(
        name="format_agent",
        instructions=f"""You are a format agent,
    you should format the answer of other agent give a markdown format.
    List all the papers to markdown points.

    Add a well-formatted title and a descriptions about the theme `{theme}`.
    """,
        model="gpt-4o-mini",
    )

    class QueryKeywords(BaseModel):
        keywords: list[str]

    query_keywords = await query_keywords_agent.run(
        "Papers about applications of LLM-based agents in biology and medicine",
        response_format=QueryKeywords,
    )

    logger.info("Query keywords:")
    pprint(query_keywords.content.keywords)

    search_results = []
    for keyword in query_keywords.content.keywords:
        try:
            results = duckduckgo_search(keyword, max_results=results_per_keyword)
            await asyncio.sleep(1)
            search_results.extend(results)
        except Exception as e:
            logger.error(e)
    merged_results = merge_search_results(search_results)

    logger.info(f"Number of items before relation check: {len(merged_results)}")

    contents = await web_crawl([result["href"] for result in merged_results])

    class ContentInfo(BaseModel):
        title: str
        url: str
        summary: str
        is_related: bool = Field(description="Whether the paper is related to the theme")
        is_a_paper: bool = Field(description="Whether the content is a journal or preprint paper")
        journal: str = Field(description="The journal name of the paper")
        time: str = Field(description="The time of the paper")

    async def process_content(content, result):
        try:
            resp = await info_extraction_agent.run(
                result["href"] + "\n" + content, response_format=ContentInfo)
            logger.info(resp.content)
            if resp.content.is_related and resp.content.is_a_paper:
                return resp.content
        except Exception as e:
            logger.error(e)
        return None

    tasks = [process_content(content, result) 
             for content, result in zip(contents, merged_results)]
    results = await asyncio.gather(*tasks)
    list_of_info = [r for r in results if r is not None]

    logger.info(f"Number of items after relation check: {len(list_of_info)}")

    markdown = await format_agent.run(list_of_info)
    logger.info("Markdown:")
    print(markdown.content)

    if output:
        with open(output, "w") as f:
            f.write(markdown.content)


if __name__ == "__main__":
    fire.Fire(main)
