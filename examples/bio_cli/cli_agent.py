import os

import fire
import pantheon.utils.log as log
from pantheon.agent import Agent
from pantheon.toolsets.python import PythonInterpreterToolSet
from pantheon.toolsets.shell import ShellToolSet
from pantheon.toolsets.file_manager import FileManagerToolSet

from dotenv import load_dotenv

HERE = os.path.dirname(__file__)


async def main():
    load_dotenv()

    log.use_rich_mode()
    log.set_level("INFO")
    log.disable("executor.engine")

    instructions = f"""
You are a bioinformatics CLI agent that helps users perform data analysis tasks.

## Available Tools
- **python**: Execute Python code for data analysis
- **bash**: Execute shell commands
- **file_manager**: Manage files (read, write, list, etc.)

## Workflow

### 1. Skill Discovery
Before starting any analysis task, use `file_manager.read_file` to read the skill index:
- Read `{HERE}/upstream_skills/SKILL.md` to understand available analysis skills
- Based on user's task, identify the appropriate skill (e.g., scrna, atac, spatial)

### 2. Task Planning
- Read the specific skill file (e.g., `upstream_skills/scrna.md`) for detailed workflow guidance
- Create a task plan using the todo tool based on the skill's recommended phases

### 3. Task Execution
- Execute tasks one by one following the skill's guidance
- Use python tool for data analysis code execution
- Use bash tool for shell commands (e.g., running bioinformatics tools)
- Mark each task as done after completion

### 4. Result Management
- Use file_manager to save analysis results and outputs
- Use file_manager.observe_images to view generated plots
- Document findings and key observations

## Key Principles
- Always read relevant skill documentation before starting analysis
- Follow the phase-based execution pattern described in skill files
- Analyze results after each step before proceeding
- Maintain persistent Python state - avoid redundant data loading
"""

    agent = Agent(
        name="CLI Agent",
        instructions=instructions,
        model="gpt-5",
    )

    await agent.toolset(PythonInterpreterToolSet("python"))
    await agent.toolset(ShellToolSet("bash"))
    await agent.toolset(FileManagerToolSet("file_manager"))

    await agent.chat()


if __name__ == "__main__":
    fire.Fire(main)
