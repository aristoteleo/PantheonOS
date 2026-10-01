"""Network-free tests for the You.com search tool in WebToolSet.

The fastmcp client is replaced with an in-memory fake, so the tests
exercise endpoint selection, auth wiring, argument passing, and
response normalization without touching the network.
"""

import json

import pytest

import fastmcp

from pantheon.toolsets.web import (
    WebToolSet,
    _parse_youcom_results,
)


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeCallToolResult:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeClient:
    """Records the transport and call arguments, returns a canned result."""

    instances = []

    def __init__(self, transport):
        self.transport = transport
        self.calls = []
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return _FakeCallToolResult(_build_payload())


def _build_payload() -> str:
    return json.dumps(
        {
            "results": {
                "web": [
                    {
                        "url": "https://example.com/first",
                        "title": "First result",
                        "description": "A first description.",
                        "page_age": "2026-01-02T03:04:05",
                    },
                    {
                        "url": "https://example.com/second",
                        "title": "Second result",
                        "description": "",
                        "page_age": None,
                        "contents": {
                            "highlights": ["Second highlight body.", "Ignored."]
                        },
                    },
                ]
            }
        }
    )


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(fastmcp, "Client", _FakeClient)
    return _FakeClient


def test_youcom_search_is_registered_as_a_tool():
    toolset = WebToolSet("web_browse")
    assert "youcom_search" in toolset.tool_functions
    # The DDG tool is untouched.
    assert "duckduckgo_search" in toolset.tool_functions


def test_parse_youcom_results_normalizes_description_and_highlights():
    results = _parse_youcom_results(_build_payload())

    assert results == [
        {
            "title": "First result",
            "href": "https://example.com/first",
            "body": "A first description.",
        },
        {
            "title": "Second result",
            "href": "https://example.com/second",
            # Empty description falls back to the first highlight.
            "body": "Second highlight body.",
        },
    ]


def test_parse_youcom_results_returns_empty_list_for_no_hits():
    assert _parse_youcom_results(json.dumps({"results": {"web": []}})) == []


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps({"unexpected": "shape"}),
        json.dumps({"results": {"news": []}}),
    ],
)
def test_parse_youcom_results_rejects_unexpected_payloads(payload):
    with pytest.raises(RuntimeError):
        _parse_youcom_results(payload)


async def test_youcom_search_uses_free_profile_without_api_key(
    monkeypatch, fake_client
):
    monkeypatch.delenv("YDC_API_KEY", raising=False)

    toolset = WebToolSet("web_browse")
    results = await toolset.youcom_search(query="hello", max_results=2)

    (client,) = fake_client.instances
    transport = client.transport
    assert transport.url == "https://api.you.com/mcp?profile=free"
    assert not transport.headers
    assert client.calls == [("you-search", {"query": "hello", "count": 2})]
    assert results[0]["href"] == "https://example.com/first"
    assert results[1]["body"] == "Second highlight body."


async def test_youcom_search_uses_authenticated_endpoint_with_api_key(
    monkeypatch, fake_client
):
    monkeypatch.setenv("YDC_API_KEY", "secret-key")

    toolset = WebToolSet("web_browse")
    await toolset.youcom_search(query="hello", max_results=5)

    (client,) = fake_client.instances
    transport = client.transport
    assert transport.url == "https://api.you.com/mcp"
    assert transport.headers == {"Authorization": "Bearer secret-key"}
    assert client.calls == [("you-search", {"query": "hello", "count": 5})]


async def test_youcom_search_caps_results_at_max_results(fake_client):
    toolset = WebToolSet("web_browse")
    results = await toolset.youcom_search(query="hello", max_results=1)

    # The canned payload has two results; max_results=1 clips to one.
    assert len(results) == 1
