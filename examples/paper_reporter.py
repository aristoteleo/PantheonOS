from pprint import pprint

import fire
from synago.agent import Agent
from synago.tools.duckduckgo import duckduckgo_search
from pydantic import BaseModel


default_theme = "The applications of LLM-based agents in biology and medicine."


async def main(theme: str = default_theme, output: str | None = None):

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
        model="gpt-4o",
    )

    def merge_search_results(results: list[dict]) -> list[dict]:
        _dict = {}
        for result in results:
            _dict[result["title"]] = result
        return list(_dict.values())

    relation_check_agent = Agent(
        name="relation_check_agent",
        instructions=f"""You are a expert in the theme: `{theme}`,
    you can check if the search result is a paper and related to the theme,
    according to the search result title.

    Please be very strict,
    only return True if the paper is very related to the theme.
    """,
        model="gpt-4o",
    )

    format_agent = Agent(
        name="format_agent",
        instructions="""You are a format agent,
    you should format the answer of other agent give a markdown format.
    List all the papers to markdown points.
    """,
        model="gpt-4o",
    )

    class QueryKeywords(BaseModel):
        keywords: list[str]

    query_keywords = await query_keywords_agent.run(
        "Papers about applications of LLM-based agents in biology and medicine",
        response_format=QueryKeywords,
    )

    print("Query keywords:")
    pprint(query_keywords.content.keywords)

    search_results = []
    for keyword in query_keywords.content.keywords:
        results = duckduckgo_search(keyword, max_results=5)
        search_results.extend(results)
    merged_results = merge_search_results(search_results)

    print("Number of items before relation check: ", len(merged_results))

    class RelationCheck(BaseModel):
        is_related: list[bool]
        is_a_paper: list[bool]

    relation_check = await relation_check_agent.run(
        str(merged_results),
        response_format=RelationCheck,
    )

    related_results = [
        result for result, is_related, is_a_paper
        in zip(merged_results, relation_check.content.is_related, relation_check.content.is_a_paper)
        if is_related and is_a_paper
    ]

    print("Number of items after relation check: ", len(related_results))
    pprint(related_results)

    markdown = await format_agent.run(str(related_results))
    print("Markdown:")
    print(markdown.content)

    if output:
        with open(output, "w") as f:
            f.write(markdown.content)


if __name__ == "__main__":
    fire.Fire(main)
