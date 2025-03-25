from tempfile import TemporaryDirectory
from pantheon.remote.memory import RemoteMemoryManager, start_memory_service
from pantheon.agent import Agent
from executor.engine import Engine, LocalJob


async def test_remote_memory():
    temp_dir = TemporaryDirectory()
    with Engine() as engine:
        job = LocalJob(start_memory_service, (temp_dir.name, "test-memory-service"))
        await engine.submit_async(job)
        await job.wait_until_status("running")

        memory_manager = RemoteMemoryManager("test-memory-service")
        memory = await memory_manager.new_memory("test-memory")
        await memory.add_messages([{"role": "user", "content": "Hello, world!"}])
        messages = await memory.get_messages()
        assert messages == [{"role": "user", "content": "Hello, world!"}]

        await memory_manager.save()
        await job.cancel()


async def test_remote_memory_agent():
    temp_dir = TemporaryDirectory()
    with Engine() as engine:
        job = LocalJob(start_memory_service, (temp_dir.name, "test-memory-service"))
        await engine.submit_async(job)
        await job.wait_until_status("running")

        memory_manager = RemoteMemoryManager("test-memory-service")
        memory = await memory_manager.new_memory("test-memory")
        agent = Agent(
            "test-agent",
            "You are a helpful assistant.",
            memory=memory,
            )
        await agent.run("Hi")
        messages = await memory.get_messages()
        assert len(messages) == 2
        await job.cancel()
