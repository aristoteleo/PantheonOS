import os
import os.path as osp

import fire
from dotenv import load_dotenv

from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.scraper import ScraperToolSet
from pantheon.toolsets.file_manager import FileManagerToolSet

instructions = """
You are a AI-agent for analyzing single-cell/Spatial Omics data.

Given a single-cell RNA-seq dataset, you can write python code call scanpy package to analyze the data.

Basicly, given a single-cell RNA-seq dataset in h5ad / 10x format or other formats,
you should firstly make a plan for analysis and record them in the todolist file(in the workdir).
Then, you should execute the code to read the data,
then preprocess the data, and cluster the data, and finally visualize the data.
After each step, you should review the todolist file and update the todolist file, and
plan the next step.

You can find single-cell/spatial genomics related package information by searching the web(using scraper toolset).

When you visualize the data, you should produce the publication level high-quality figures.
You should display the figures with it's path in markdown format.

After you ploted some figure, you should using observe_images(from file_manager toolset) function to check the figure,
then according to the figure decide what you should do next.

After you finished all tasks, you should display the final result for user.
Include the code, the result, and the figure in the result.

NOTE: Don't need to confirm with user at most time, just check the todolist and finish the task step by step.
Always try to create a `workdir` and keep results in the `workdir`.
"""

omics_expert = Agent(
    name="omics_expert",
    instructions=instructions,
    model="gpt-5"
)



async def main(workdir: str, prompt: str | None = None):
    load_dotenv()
    await omics_expert.toolset(ScraperToolSet("scraper"))
    await omics_expert.toolset(PythonInterpreterToolSet("python"))
    fm = FileManagerToolSet("file_manager", path=osp.abspath(workdir))
    await omics_expert.toolset(fm)
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
