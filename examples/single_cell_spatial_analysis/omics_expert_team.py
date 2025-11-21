import os
import os.path as osp
import sys

import yaml
import fire
from dotenv import load_dotenv
import loguru

from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.web import WebToolSet
from pantheon.toolsets.file_manager import FileManagerToolSet
from pantheon.toolsets.shell import ShellToolSet
from pantheon.toolsets.notebook import IntegratedNotebookToolSet
from pantheon.team.aat import AgentAsToolTeam
from pantheon.utils.display import print_agent_message


TIMEOUT_TOOL = 24*60*60


async def load_agent(path: str):
    with open(path, "r") as f:
        contents = f.read()
    _parts = contents.split("---\n")
    conf_str = _parts[1]
    conf = yaml.safe_load(conf_str)
    instructions = "---".join(_parts[2:])
    conf["instructions"] = instructions
    if "toolsets" in conf:
        toolsets_str: list[str] = conf.pop("toolsets")
    else:
        toolsets_str = []
    str2cls = {
        "python_interpreter": PythonInterpreterToolSet,
        "shell": ShellToolSet,
        "file_manager": FileManagerToolSet,
        "notebook": IntegratedNotebookToolSet,
        "web": WebToolSet,
    }
    conf['tool_timeout'] = TIMEOUT_TOOL
    agent = Agent(**conf)
    for t in toolsets_str:
        toolset = str2cls[t](t)
        await agent.toolset(toolset)
    return agent


async def main(workdir: str, prompt: str | None = None, log_level: str = "WARNING"):
    loguru.logger.remove()
    loguru.logger.add(sys.stdout, level=log_level)

    load_dotenv()
    workpath = osp.abspath(workdir)

    # ---------- Load agents ----------
    leader = await load_agent("agents/leader.md")
    system_manager = await load_agent("agents/system_manager.md")
    analysis_expert = await load_agent("agents/analysis_expert.md")
    biologist = await load_agent("agents/biologist.md")
    reporter = await load_agent("agents/reporter.md")

    # ---------- Team ----------
    team = AgentAsToolTeam(leader, [
        system_manager,
        analysis_expert,
        biologist,
        reporter,
    ])

    # ---------- Task execution ----------

    if prompt is None:
        prompt_path = osp.join(workpath, "prompt.md")
        try:
            with open(prompt_path, "r") as f:
                prompt = f.read()
                prompt += "\n\nWorkdir: " + osp.join(workpath, "workdir")
        except FileNotFoundError:
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    def process_step_message(msg: dict):
        agent_name = msg.get("agent_name", "Agent?")
        try:
            print_agent_message(agent_name, msg)
        except Exception:
            print(agent_name+":\n", msg)

    await team.run(prompt, process_step_message=process_step_message)


if __name__ == "__main__":
    fire.Fire(main)
