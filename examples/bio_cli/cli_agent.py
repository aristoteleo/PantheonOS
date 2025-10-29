import os

import fire
import pantheon.utils.log as log
from pantheon.agent import Agent
from pantheon.repl import Repl
from pantheon.toolsets.todo import TodoToolSet
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.r import RInterpreterToolSet
from pantheon.toolsets.julia import JuliaInterpreterToolSet
from pantheon.toolsets.shell import ShellToolSet
from pantheon.toolsets.workflow import WorkflowToolSet

HERE = os.path.dirname(__file__)


async def main():
    log.use_rich_mode()
    log.set_level("INFO")
    log.disable("executor.engine")

    bio_workflow_path = os.path.join(HERE, "bio_workflows")

    instructions = """
    You are a CLI agent that can help user perform data analysis.

    Before performing any analysis, you should do planning with the todo tool.
    When planning, you should use the list workflow tool to get the workflow of the analysis.
    After planning, you should do task executions one by one.
    Before each task execution, you should use the use_workflow tool to get the specific workflow information related to the current task.
    After each task execution, you should use the todo tool to mark the task as done.
    Then you can use python/shell/r/julia tool to perform any code execution according to the information you have.
    If there are some results or outputs, you should use the file editor tool to save the results or outputs.

    During the analysis, you should use the shell tool to perform any shell commands.
    You should use the python tool to perform any python code execution.
    You should use the r tool to perform any r code execution.
    You should use the julia tool to perform any julia code execution.
    """

    agent = Agent(
        name="CLI Agent",
        instructions=instructions,
        model="gpt-5",
    )
    agent.toolset(TodoToolSet("todo"))
    agent.toolset(PythonInterpreterToolSet("python"))
    agent.toolset(RInterpreterToolSet("r"))
    agent.toolset(JuliaInterpreterToolSet("julia"))
    agent.toolset(ShellToolSet("bash"))
    agent.toolset(WorkflowToolSet("bio-workflow", bio_workflow_path))

    repl = Repl(agent)

    repl.register_handler(bio_workflow_path)

    await repl.run()


if __name__ == "__main__":
    fire.Fire(main)
