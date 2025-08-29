import fire
from loguru import logger
from pantheon.agent import Agent
from pantheon.repl import Repl
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.r import RInterpreterToolSet
from pantheon.toolsets.julia import JuliaInterpreterToolSet
from pantheon.toolsets.shell import ShellToolSet

async def main():
    logger.disable("executor.engine")
    agent = Agent(
        name="CLI Agent",
        instructions="""You are a CLI agent that can help user perform data analysis.""",
    )
    agent.toolset(PythonInterpreterToolSet("python"))
    agent.toolset(RInterpreterToolSet("r"))
    agent.toolset(JuliaInterpreterToolSet("julia"))
    agent.toolset(ShellToolSet("bash"))

    repl = Repl(agent)

    await repl.run()


if __name__ == "__main__":
    fire.Fire(main)
