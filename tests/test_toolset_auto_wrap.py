"""Test automatic wrapping of ToolSet into LocalProvider"""

import pytest

from pantheon.agent import Agent
from pantheon.providers import LocalProvider
from pantheon.toolset import ToolSet, tool


class SampleToolSet(ToolSet):
    """A sample toolset for testing"""

    def __init__(self):
        super().__init__(name="test_toolset")

    @tool
    async def greet(self, name: str) -> str:
        """Greet someone

        Args:
            name: Name of the person to greet

        Returns:
            Greeting message
        """
        return f"Hello, {name}!"

    @tool
    async def calculate(self, x: int, y: int) -> int:
        """Add two numbers

        Args:
            x: First number
            y: Second number

        Returns:
            Sum of x and y
        """
        return x + y


@pytest.mark.asyncio
async def test_toolset_auto_wrapped_in_local_provider():
    """Test that ToolSet is automatically wrapped in LocalProvider"""
    toolset = SampleToolSet()
    agent = Agent(
        name="test_agent",
        instructions="You are a test assistant.",
        model="gpt-4o-mini",
    )

    # Add ToolSet directly (should be wrapped automatically)
    await agent.toolset(toolset)

    # Verify it was wrapped in LocalProvider and added to providers
    assert "test_toolset" in agent.providers
    provider = agent.providers["test_toolset"]
    assert isinstance(provider, LocalProvider)
    assert provider.toolset is toolset


@pytest.mark.asyncio
async def test_toolset_auto_wrap_initializes_provider():
    """Test that auto-wrapped provider is initialized"""
    toolset = SampleToolSet()
    agent = Agent(
        name="test_agent",
        instructions="You are a test assistant.",
        model="gpt-4o-mini",
    )

    # Add ToolSet
    await agent.toolset(toolset)

    # Verify provider is initialized (has cached tools)
    provider = agent.providers["test_toolset"]
    assert provider._tools_cache is not None or len(provider._tool_descriptions) > 0


@pytest.mark.asyncio
async def test_toolset_auto_wrap_tools_available():
    """Test that tools from auto-wrapped ToolSet are available"""
    toolset = SampleToolSet()
    agent = Agent(
        name="test_agent",
        instructions="You are a test assistant.",
        model="gpt-4o-mini",
    )

    # Add ToolSet
    await agent.toolset(toolset)

    # Get tools for LLM
    tools = await agent.get_tools_for_llm()

    # Should have 2 tools: greet and calculate
    assert len(tools) >= 2

    tool_names = {tool["function"]["name"] for tool in tools}
    # Tools are prefixed with toolset name when added via provider
    assert "test_toolset__greet" in tool_names
    assert "test_toolset__calculate" in tool_names


@pytest.mark.asyncio
async def test_toolset_and_provider_both_work():
    """Test that both ToolSet and LocalProvider can be added"""
    toolset1 = SampleToolSet()

    class AnotherToolSet(ToolSet):
        def __init__(self):
            super().__init__(name="another_toolset")

        @tool
        async def ping(self) -> str:
            return "pong"

    toolset2 = AnotherToolSet()
    provider2 = LocalProvider(toolset2)

    agent = Agent(
        name="test_agent",
        instructions="You are a test assistant.",
        model="gpt-4o-mini",
    )

    # Add ToolSet (auto-wrapped) and LocalProvider (direct)
    await agent.toolset(toolset1)
    await agent.toolset(provider2)

    # Both should be in providers
    assert "test_toolset" in agent.providers
    assert "another_toolset" in agent.providers

    # Both should be LocalProvider instances
    assert isinstance(agent.providers["test_toolset"], LocalProvider)
    assert isinstance(agent.providers["another_toolset"], LocalProvider)

    # Get all tools
    tools = await agent.get_tools_for_llm()
    tool_names = {tool["function"]["name"] for tool in tools}

    # Should have tools from both toolsets (with provider name prefix)
    assert "test_toolset__greet" in tool_names  # from toolset1
    assert "test_toolset__calculate" in tool_names  # from toolset1
    assert "another_toolset__ping" in tool_names  # from toolset2


@pytest.mark.asyncio
async def test_direct_provider_call_after_auto_wrap():
    """Test that we can call tools through auto-wrapped provider"""
    toolset = SampleToolSet()
    agent = Agent(
        name="test_agent",
        instructions="You are a test assistant.",
        model="gpt-4o-mini",
    )

    # Add ToolSet (auto-wrapped)
    await agent.toolset(toolset)

    # Get the auto-wrapped provider
    provider = agent.providers["test_toolset"]

    # Call tools directly through provider
    result1 = await provider.call_tool("greet", {"name": "Alice"})
    assert result1 == "Hello, Alice!"

    result2 = await provider.call_tool("calculate", {"x": 10, "y": 20})
    assert result2 == 30
