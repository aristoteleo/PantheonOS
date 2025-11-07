"""Example: Using LocalProvider for in-memory ToolSet calls

This example demonstrates how to use LocalProvider to add a ToolSet instance
directly to an Agent without requiring remote connections.
"""

import asyncio

from pantheon.agent import Agent
from pantheon.providers import LocalProvider
from pantheon.toolset import ToolSet, tool


# Define a simple ToolSet
class CalculatorToolSet(ToolSet):
    """A simple calculator toolset for demonstration"""

    def __init__(self):
        super().__init__(name="calculator")

    @tool
    async def add(self, a: float, b: float) -> float:
        """Add two numbers together

        Args:
            a: First number
            b: Second number

        Returns:
            Sum of a and b
        """
        return a + b

    @tool
    async def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers

        Args:
            a: First number
            b: Second number

        Returns:
            Product of a and b
        """
        return a * b

    @tool
    async def power(self, base: float, exponent: float) -> float:
        """Raise a number to a power

        Args:
            base: The base number
            exponent: The exponent

        Returns:
            base raised to the power of exponent
        """
        return base**exponent


async def main():
    # Create a ToolSet instance
    calculator = CalculatorToolSet()

    # Create a LocalProvider with the toolset instance
    local_provider = LocalProvider(calculator)

    # Create an Agent
    agent = Agent(
        name="math_assistant",
        instructions="You are a helpful math assistant. Use the calculator tools to help users with calculations.",
        model="gpt-4o-mini",
    )

    # Add the LocalProvider to the agent
    await agent.toolset(local_provider)

    # Now the agent can use the calculator tools directly in-memory
    print("=" * 60)
    print("Example 1: Simple addition")
    print("=" * 60)
    result = await agent.run("What is 15 + 27?")
    print(f"Result: {result}\n")

    print("=" * 60)
    print("Example 2: Complex calculation")
    print("=" * 60)
    result = await agent.run("Calculate 3 to the power of 4, then multiply by 2")
    print(f"Result: {result}\n")

    print("=" * 60)
    print("Example 3: Multi-step calculation")
    print("=" * 60)
    result = await agent.run(
        "First add 10 and 5, then multiply the result by 3, then raise it to the power of 2"
    )
    print(f"Result: {result}\n")


if __name__ == "__main__":
    asyncio.run(main())
