from synago.tools.duckduckgo import duckduckgo_search


def test_duckduckgo_search():
    results = duckduckgo_search("cats dogs", max_results=5)
    assert len(results) == 5
    for result in results:
        assert isinstance(result, dict)
        assert "title" in result
