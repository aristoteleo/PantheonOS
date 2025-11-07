"""Tests for LocalProvider"""

import pytest

from pantheon.agent import Agent
from pantheon.providers import LocalProvider
from pantheon.toolset import ToolSet, tool


class SimpleToolSet(ToolSet):
    """A simple toolset for testing"""

    def __init__(self):
        super().__init__(name="simple_tools")
        self.call_count = 0

    @tool
    async def echo(self, message: str) -> str:
        """Echo back a message

        Args:
            message: The message to echo

        Returns:
            The same message
        """
        self.call_count += 1
        return f"Echo: {message}"

    @tool
    async def add_numbers(self, a: int, b: int) -> int:
        """Add two numbers

        Args:
            a: First number
            b: Second number

        Returns:
            Sum of a and b
        """
        self.call_count += 1
        return a + b


@pytest.mark.asyncio
async def test_local_provider_initialization():
    """Test LocalProvider initialization"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    assert provider.toolset_name == "simple_tools"
    assert provider._tools_cache is None

    # Initialize provider
    await provider.initialize()

    # After initialization, tool descriptions should be cached
    assert len(provider._tool_descriptions) > 0


@pytest.mark.asyncio
async def test_local_provider_list_tools():
    """Test listing tools from LocalProvider"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    await provider.initialize()
    tools = await provider.list_tools()

    # Should have 2 tools: echo and add_numbers
    assert len(tools) == 2

    tool_names = {tool.name for tool in tools}
    assert "echo" in tool_names
    assert "add_numbers" in tool_names

    # Check tool info
    echo_tool = next(t for t in tools if t.name == "echo")
    assert "Echo back a message" in echo_tool.description
    assert echo_tool.inputSchema is not None

    # Second call should use cache
    tools2 = await provider.list_tools()
    assert tools2 is tools  # Same object from cache


@pytest.mark.asyncio
async def test_local_provider_call_tool():
    """Test calling tools through LocalProvider"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    await provider.initialize()

    # Call echo tool
    result = await provider.call_tool("echo", {"message": "Hello World"})
    assert result == "Echo: Hello World"
    assert toolset.call_count == 1

    # Call add_numbers tool
    result = await provider.call_tool("add_numbers", {"a": 5, "b": 3})
    assert result == 8
    assert toolset.call_count == 2


@pytest.mark.asyncio
async def test_local_provider_with_agent():
    """Test LocalProvider integration with Agent"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    agent = Agent(
        name="test_agent",
        instructions="You are a test assistant.",
        model="gpt-4o-mini",
    )

    # Add provider to agent
    await agent.toolset(provider)

    # Verify provider is added
    assert "simple_tools" in agent.providers
    assert agent.providers["simple_tools"] is provider

    # Verify tools can be retrieved
    tools = await agent.get_tools_for_llm()
    assert len(tools) > 0

    # Note: Full agent.run() test would require API key and make actual LLM calls
    # So we just verify the provider is properly added


@pytest.mark.asyncio
async def test_local_provider_parameter_filtering():
    """Test that LocalProvider filters parameters correctly"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    await provider.initialize()

    # Call with extra parameters that should be filtered out
    result = await provider.call_tool(
        "echo",
        {
            "message": "Test",
            "extra_param": "should_be_ignored",
            "context_variables": {},  # Should be kept (in _SKIP_PARAMS)
        },
    )

    # Should still work, extra params filtered
    assert result == "Echo: Test"


@pytest.mark.asyncio
async def test_local_provider_tool_not_found():
    """Test calling non-existent tool"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    await provider.initialize()

    # Try to call non-existent tool
    with pytest.raises(AttributeError, match="Tool 'nonexistent' not found"):
        await provider.call_tool("nonexistent", {})


@pytest.mark.asyncio
async def test_local_provider_shutdown():
    """Test LocalProvider shutdown"""
    toolset = SimpleToolSet()
    provider = LocalProvider(toolset)

    await provider.initialize()

    # Shutdown should work without errors
    await provider.shutdown()

    # Should still be usable after shutdown (no remote connections to close)
    result = await provider.call_tool("echo", {"message": "After shutdown"})
    assert result == "Echo: After shutdown"
