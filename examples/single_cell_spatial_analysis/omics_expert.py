import os
import os.path as osp

import fire
from dotenv import load_dotenv

from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.workflow import WorkflowToolSet
from pantheon.toolsets.scraper import ScraperToolSet
from pantheon.toolsets.todolist import TodoListToolSet
from pantheon.toolsets.plan_mode import PlanModeToolSet

instructions = """
You are a AI-agent for analyzing single-cell/Spatial Omics data.

Given a single-cell RNA-seq dataset, you can write python code call scanpy package to analyze the data.

Basicly, given a single-cell RNA-seq dataset in h5ad / 10x format or other formats,
you should firstly make a plan for analysis and record them in the todolist tool.
Then, you should execute the code to read the data,
then preprocess the data, and cluster the data, and finally visualize the data.
After each step, you should review the todos and update the todos, and
plan the next step.
You can find single-cell/spatial genomics related package information by searching the web.

When you visualize the data, you should produce the publication level high-quality figures.
You should display the figures with it's path in markdown format.

After you ploted some figure, you should using view_image function to check the figure,
then according to the figure decide what you should do next.

After you finished the task, you should display the final result for user.
Include the code, the result, and the figure in the result.

NOTE: Don't need to confirm with user at most time, just do the task.
"""

omics_expert = Agent(
    name="omics_expert",
    instructions="You are an expert in omics data analysis.",
    model="gpt-5"
)



async def main(workdir: str, prompt: str | None = None):
    load_dotenv()
    await omics_expert.toolset(PythonInterpreterToolSet("python"))
    #workflow_path = osp.join(osp.dirname(__file__), "workflows")
    #await omics_expert.toolset(WorkflowToolSet("workflow", workflow_path=workflow_path))
    await omics_expert.toolset(TodoListToolSet("todolist"))
    await omics_expert.toolset(PlanModeToolSet("plan_mode"))
    await omics_expert.toolset(ScraperToolSet("scraper"))
    if prompt is None:
        try:
            with open(osp.join(workdir, "prompt.md"), "r") as f:
                prompt = f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Prompt file not found: {osp.join(workdir, 'prompt.md')}")

    os.chdir(workdir)
    await omics_expert.chat(prompt)


if __name__ == "__main__":
    fire.Fire(main)
