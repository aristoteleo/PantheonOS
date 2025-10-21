from pantheon.agent import Agent
import os
from fastmcp import Client

async def test_call_mcp():
    agent = Agent(
        name="test",
        instructions="You are a test agent.",
    )
    tmp_file = "tmp_my_server.py"
    with open(tmp_file, "w") as f:
        f.write("""
from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")

@mcp.tool
def add(a: int, b: int) -> tuple[int, int]:
    \"\"\"Add two numbers\"\"\"
    return a + b, a - b

@mcp.tool
def hello(name: str) -> str:
    \"\"\"Say hello to someone\"\"\"
    return f\"Hello, {name}!\"

if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run())
        """)
    async with Client(tmp_file) as client:
        from pantheon.providers import MCPProvider
        mcp_provider = MCPProvider(client=client)
        await mcp_provider.initialize()
        await agent.mcp("mcp", mcp_provider)
    resp = await agent.run("What is 2 + 2?, call add tool to calculate the result")
    assert "4" in resp.content
    os.remove(tmp_file)

