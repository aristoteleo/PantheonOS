import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.prompt import Prompt

from ..team import Team
from ..utils.misc import print_agent, print_agent_message


class Repl:
    def __init__(self, team: Team):

        self.team = team
        self.console = Console()

    async def print_greeting(self):
        self.console.print(
            "[bold]Welcome to the Pantheon REPL![/bold]\n" +
            "You can start by typing a message or type 'exit' to exit.\n"
        )
        # print team agents
        self.console.print("[bold]Team agents:[/bold]")
        for agent in self.team.agents.values():
            await print_agent(agent, self.console)
        self.console.print()

    async def print_message(self):
        while True:
            message = await self.team.events_queue.get()
            print_agent_message(
                message["agent_name"],
                message["event"],
                self.console,
                print_assistant_message=True,
            )

    async def run(self, message: str | dict | None = None):
        import logging
        logging.getLogger().setLevel(logging.WARNING)
        import loguru
        loguru.logger.remove()
        loguru.logger.add(sys.stdout, level="WARNING")

        await self.team.async_setup()
        await self.print_greeting()
        await asyncio.sleep(0.1)

        gather_task = asyncio.create_task(self.team.gather_events())
        print_task = asyncio.create_task(self.print_message())
        await asyncio.sleep(0.1)

        def ask_user():
            message = Prompt.ask("[red][bold]User[/bold][/red]")
            self.console.print()
            return message

        if message is None:
            message = ask_user()
            if message == "exit":
                return
        else:
            self.console.print(f"[red][bold]User[/bold][/red]: {message}\n")

        while True:
            await self.team.run(message)
            await asyncio.sleep(0.5)
            self.console.print()
            message = ask_user()
            if message == "exit":
                break

        gather_task.cancel()
        print_task.cancel()
