from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel

from .agent import Agent


class Task:
    def __init__(self, name: str, goal: str):
        self.name = name
        self.goal = goal


class BoolResponse(BaseModel):
    result: bool


class TasksSolver:
    def __init__(
            self,
            tasks: list[Task],
            agent: Agent,
        ):
        self.tasks = tasks
        self.agent = agent
        self.console = Console()

    def process_step_message(self, message: dict):

        def print_tool_message(message: str):
            panel = Panel(message, title="Tool Message")
            self.console.print(panel)

        if tool_calls := message.get("tool_calls"):
            for call in tool_calls:
                print_tool_message(
                    f"[bold]Agent [blue]{self.agent.name}[/blue] is using tool "
                    f"[green]{call.get('function', {}).get('name')}[/green]"
                    f" with arguments [yellow]{call.get('function', {}).get('arguments')}[/yellow]"
                    "[/bold]"
                )
        if message.get("role") == "tool":
            print_tool_message(
                f"[bold]Agent [blue]{self.agent.name}[/blue] got result from tool "
                f"[green]{message.get('tool_name')}[/green]:[/bold] "
                f"[yellow]{message.get('content')}[/yellow]"
            )

    async def solve(self):
        import logging
        logging.getLogger().setLevel(logging.WARNING)

        for i, task in enumerate(self.tasks):
            self.console.print(f"Solving task [blue]{task.name}[/blue] ({i+1}/{len(self.tasks)}): [yellow]{task.goal}[/yellow]")
            prompt = f"Solve the task: {task.name}\nGoal: {task.goal}"
            resp = await self.agent.run(prompt, process_step_message=self.process_step_message)
            self.console.print(resp.content)
            while True:
                resp = await self.agent.run(f"The task {task.name} has been solved or not?", response_format=BoolResponse)
                if resp.content.result:
                    self.console.print(f"[green]Task [blue]{task.name}[/blue] has been solved.[/green]")
                    break
                else:
                    self.console.print("[red]The task has not been solved, will try again.[/red]")
                    resp = await self.agent.run("The task has not been solved, please analyze the reason and try again.")
                    self.console.print(resp.content)
