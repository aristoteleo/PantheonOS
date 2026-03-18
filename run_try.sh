python3 -c "
import os, asyncio
os.environ['LLM_API_BASE'] = 'http://localhost:11434/v1'
os.environ['LLM_API_KEY'] = 'ollama'

from pantheon.agent import Agent

agent = Agent(
    name='test',
    instructions='You are helpful.',
    model='qwen2.5:72b-instruct-q4_K_M',
)

async def test():
    result = await agent.run('Say hello in one word.')
    print(result.content)

asyncio.run(test())
"