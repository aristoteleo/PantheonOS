"""Example: Automatic ToolSet wrapping in LocalProvider

This example demonstrates the automatic wrapping feature where you can
directly add a ToolSet instance to an Agent, and it will be automatically
wrapped in a LocalProvider for unified provider-based routing.
"""

import asyncio

from pantheon.agent import Agent
from pantheon.toolset import ToolSet, tool


# Define a simple ToolSet
class WeatherToolSet(ToolSet):
    """A simple weather toolset for demonstration"""

    def __init__(self):
        super().__init__(name="weather")

    @tool
    async def get_temperature(self, city: str) -> str:
        """Get the current temperature for a city

        Args:
            city: The name of the city

        Returns:
            Temperature information
        """
        # Simulated weather data
        temps = {
            "Beijing": "15°C",
            "Shanghai": "20°C",
            "Guangzhou": "25°C",
            "Shenzhen": "26°C",
        }
        return f"The temperature in {city} is {temps.get(city, 'unknown')}."

    @tool
    async def get_forecast(self, city: str, days: int = 3) -> str:
        """Get weather forecast for a city

        Args:
            city: The name of the city
            days: Number of days to forecast (default: 3)

        Returns:
            Weather forecast
        """
        return f"{days}-day forecast for {city}: Mostly sunny with occasional clouds."


async def main():
    print("=" * 60)
    print("Auto-Wrapping ToolSet Example")
    print("=" * 60)
    print()

    # Create a ToolSet instance
    weather = WeatherToolSet()

    # Create an Agent
    agent = Agent(
        name="weather_assistant",
        instructions="You are a helpful weather assistant. Use the weather tools to help users get weather information.",
        model="gpt-4o-mini",
    )

    # Add the ToolSet directly - it will be auto-wrapped in LocalProvider! ✨
    print("Adding ToolSet to Agent (will be auto-wrapped)...")
    await agent.toolset(weather)
    print(f"✓ ToolSet added as provider: {list(agent.providers.keys())}")
    print()

    # Verify it was wrapped in LocalProvider
    from pantheon.providers import LocalProvider

    provider = agent.providers["weather"]
    print(f"Provider type: {type(provider).__name__}")
    print(f"Is LocalProvider: {isinstance(provider, LocalProvider)}")
    print()

    # Now the agent can use the weather tools
    print("=" * 60)
    print("Example 1: Get temperature")
    print("=" * 60)
    result = await agent.run("What's the temperature in Beijing?")
    print(f"Result: {result}\n")

    print("=" * 60)
    print("Example 2: Get forecast")
    print("=" * 60)
    result = await agent.run("Give me the 5-day weather forecast for Shanghai")
    print(f"Result: {result}\n")

    print("=" * 60)
    print("Example 3: Multiple cities")
    print("=" * 60)
    result = await agent.run(
        "Compare the temperatures in Beijing, Shanghai, and Guangzhou"
    )
    print(f"Result: {result}\n")


if __name__ == "__main__":
    asyncio.run(main())
