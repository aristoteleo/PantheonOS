import os
import os.path as osp

import fire

from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.workflow import WorkflowToolSet

instructions = """
You are a AI-agent for analyzing single-cell/Spatial Omics data.

Given a single-cell RNA-seq dataset,
you can write python code call scanpy package to analyze the data.


You can find single-cell/spatial genomics related package information in the vector database,
you can use it to analyze the data.
In most time, you should query the vector database to find the related package information
to support your analysis. And when you meet some error, you should try to query the vector database
to find the related package information to support your analysis.

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

omics_expert.toolset(PythonInterpreterToolSet("python"))
workflow_path = osp.join(osp.dirname(__file__), "workflows")
omics_expert.toolset(WorkflowToolSet("workflow", workflow_path=workflow_path))


async def main(workdir: str, prompt: str | None = None):
    os.chdir(workdir)
    if prompt is None:
        try:
            with open(osp.join(workdir, "prompt.md"), "r") as f:
                prompt = f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Prompt file not found: {osp.join(workdir, 'prompt.md')}")
    
    await omics_expert.chat(prompt)


if __name__ == "__main__":
    fire.Fire(main)
