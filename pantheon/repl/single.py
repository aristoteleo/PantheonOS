import asyncio

from rich.console import Console
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.live import Live
from rich.panel import Panel

from ..agent import Agent


class Repl:
    def __init__(self, agent: Agent):
        """REPL for a single agent."""
        self.agent = agent
        self.console = Console()

    def print_greeting(self):
        self.console.print(
            "[bold]Welcome to the Pantheon REPL![/bold]\n" +
            "You can start by typing a message or type 'exit' to exit.\n"
        )
        # print current agent
        self.console.print("[bold]Current agent:[/bold]")
        self.console.print(f"  - [blue]{self.agent.name}[/blue]")
        # print agent instructions
        self.console.print(f"    - [green]Instructions:[/green] {self.agent.instructions}")
        # print agent tools
        if self.agent.functions:
            self.console.print("    - [green]Tools:[/green]")
            for func in self.agent.functions.values():
                self.console.print(f"      - {func.__name__}")
        if self.agent.toolset_proxies:
            self.console.print("    - [green]Remote ToolSets:[/green]")
            for proxy in self.agent.toolset_proxies.values():
                self.console.print(f"      - {proxy.service_info.service_name}")

        self.console.print()

    async def run(self, message: str | dict | None = None):
        import logging
        logging.getLogger().setLevel(logging.WARNING)

        self.print_greeting()

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
            self.console.print(f"[blue][bold]{self.agent.name}[/bold][/blue]: ")
            content = ""
            markdown = Markdown(content)

            with Live(markdown, refresh_per_second=4) as live:
                def process_chunk(chunk: dict):
                    nonlocal content
                    content += chunk.get("content", "") or ""
                    live.update(Markdown(content))

                def process_step_message(message: dict):

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

                await self.agent.run(
                    message,
                    process_chunk=process_chunk,
                    process_step_message=process_step_message,
                )

            self.console.print()
            message = ask_user()
            if message == "exit":
                break
            if message == "clear":
                content = ""
                markdown = Markdown(content)
                live.update(markdown)
                continue


if __name__ == "__main__":
    agent = Agent(
        "agent",
        "You are a helpful assistant."
    )
    repl = Repl(agent)
    asyncio.run(repl.run())