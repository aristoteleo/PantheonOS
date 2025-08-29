import os

import fire
from pantheon.utils.log import logger
from pantheon.agent import Agent
from pantheon.repl import Repl
from pantheon.toolsets.todo import TodoToolSet
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.r import RInterpreterToolSet
from pantheon.toolsets.julia import JuliaInterpreterToolSet
from pantheon.toolsets.shell import ShellToolSet

async def main():
    logger.use_rich = True
    logger.set_level("INFO")

    agent = Agent(
        name="CLI Agent",
        instructions="""You are a CLI agent that can help user perform data analysis.""",
    )
    agent.toolset(TodoToolSet("todo"))
    agent.toolset(PythonInterpreterToolSet("python"))
    agent.toolset(RInterpreterToolSet("r"))
    agent.toolset(JuliaInterpreterToolSet("julia"))
    agent.toolset(ShellToolSet("bash"))

    repl = Repl(agent)

    bio_handler_template_path = os.path.join(os.path.dirname(__file__), "bio.toml")
    repl.register_handler(bio_handler_template_path)

    await repl.run()


if __name__ == "__main__":
    fire.Fire(main)
