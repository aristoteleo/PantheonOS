"""
This is an example of using pantheon to build an agentic pipeline to generate a markdown report of papers about a theme.

# Install the dependencies
```bash
pip install pantheon
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
from pantheon.smart_func import smart_func
from pantheon.tools.web_browse.duckduckgo import duckduckgo_search
from pantheon.tools.web_browse.web_crawl import web_crawl
from loguru import logger
from pydantic import BaseModel, Field


default_theme = "The applications of LLM-based agents in biology and medicine."


async def main(
    theme: str = default_theme,
    output: str | None = None,
    model: str = "gpt-4o-mini",
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

    @smart_func(model=model)
    async def gen_query_keywords(theme: str) -> list[str]:
        """You are a search engine expert,
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
        """

    def merge_search_results(results: list[dict]) -> list[dict]:
        _dict = {}
        for result in results:
            _dict[result["title"]] = result
        return list(_dict.values())

    class ContentInfo(BaseModel):
        title: str
        url: str
        summary: str
        journal: str = Field(description="The journal name of the paper")
        time: str = Field(description="The time of the paper")

    @smart_func(model=model)
    async def check_content_is_paper(content: str) -> bool:
        """You should check if the content is a journal or preprint paper."""

    @smart_func(model=model)
    async def extract_paper_info(content: str) -> ContentInfo:
        """You should extract the paper title, summary, journal, time from the page content."""

    @smart_func(model=model)
    async def check_paper_relation(info: ContentInfo, theme: str) -> bool:
        """You should check if the paper is related to the theme. """

    @smart_func(model=model)
    async def format_paper_info(info: list[ContentInfo]) -> str:
        """You should format the answer of other agent give a markdown format.
        List all the papers to markdown points.

        Add a well-formatted title and a descriptions about the theme `{theme}`.
        """

    query_keywords = await gen_query_keywords(theme)

    logger.info("Query keywords:")
    pprint(query_keywords)

    search_results = []
    search_interval = 0.5
    for keyword in query_keywords:
        try:
            results = duckduckgo_search(keyword, max_results=results_per_keyword)
            await asyncio.sleep(search_interval)
            search_results.extend(results)
        except Exception as e:
            logger.error(e)
    merged_results = merge_search_results(search_results)

    logger.info(f"Number of items before relation check: {len(merged_results)}")

    contents = await web_crawl([result["href"] for result in merged_results])

    async def process_content(content, result):
        try:
            is_paper = await check_content_is_paper(content)
            if not is_paper:
                return None
            info = await extract_paper_info(result["href"] + "\n" + content)
            logger.info(info)
            is_related = await check_paper_relation(info, theme)
            if not is_related:
                return None
            return info
        except Exception as e:
            logger.error(e)
        return None

    tasks = [
        process_content(content, result) 
        for content, result in zip(contents, merged_results)
    ]
    results = await asyncio.gather(*tasks)
    list_of_info = [r for r in results if r is not None]

    logger.info(f"Number of items after relation check: {len(list_of_info)}")

    markdown = await format_paper_info(list_of_info)
    logger.info("Markdown:")
    print(markdown)

    if output:
        with open(output, "w") as f:
            f.write(markdown)


if __name__ == "__main__":
    fire.Fire(main)
