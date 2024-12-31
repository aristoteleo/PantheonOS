from duckduckgo_search import DDGS


def duckduckgo_search(
        query: str,
        max_results: int = 10,
        time_limit: str | None = None,
        ):
    """Search the web for the query.


    Args:
        query: The query to search for.
        max_results: The maximum number of results to return.
        time_limit: The time limit for the search. d, w, m, y. Defaults to None.
    """
    with DDGS() as ddgs:
        results = ddgs.text(
            query,
            max_results=max_results,
            timelimit=time_limit,
        )
    return results
