import asyncio

from rich.console import Console

from .agent import Agent
from .utils.misc import print_agent_message


class Task:
    def __init__(self, name: str, goal: str):
        self.name = name
        self.goal = goal


class TasksSolver:
    def __init__(
            self,
            tasks: list[Task] | Task,
            agent: Agent,
        ):
        if isinstance(tasks, Task):
            tasks = [tasks]
        self.tasks = tasks
        self.agent = agent
        self.console = Console()

    async def process_agent_messages(self):
        while True:
            message = await self.agent.events_queue.get()
            print_agent_message(self.agent.name, message, self.console)

    async def solve(self):
        import logging
        logging.getLogger().setLevel(logging.WARNING)

        print_task = asyncio.create_task(self.process_agent_messages())

        for i, task in enumerate(self.tasks):
            self.console.print(f"Solving task [blue]{task.name}[/blue] ({i+1}/{len(self.tasks)}): [yellow]{task.goal}[/yellow]")
            prompt = f"Solve the task: {task.name}\nGoal: {task.goal}"
            resp = await self.agent.run(prompt)
            self.console.print(resp.content)
            while True:
                await self.agent.run(f"The task {task.name} has been solved or not? If no, please tell me the reason.")
                resp = await self.agent.run(f"The task {task.name} has been solved or not?", response_format=bool)
                if resp.content:

                    self.console.print(f"[green]Task [blue]{task.name}[/blue] has been solved.[/green]")
                    break
                else:
                    self.console.print("[red]The task has not been solved, will try again.[/red]")
                    resp = await self.agent.run("The task has not been solved, please analyze the reason and try again.")
                    self.console.print(resp.content)

        print_task.cancel()
