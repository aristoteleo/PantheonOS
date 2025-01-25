import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Header, Static, Input, Button, Markdown
from textual.containers import Vertical, Horizontal, VerticalScroll

from ..meeting import (
    Meeting, Message, message_to_record,
    ToolEvent, ToolResponseEvent, ThinkingEvent, Record,
)


class Repl(App):
    TITLE = "Pantheon Meeting"

    CSS = """
    Screen {
        background: black;
    }

    .message-display {
        padding: 1;
        border: solid;
        height: 85%;
        overflow: auto;
    }

    .message-input {
        padding: 1;
        border-top: solid;
    }

    .message-input Input {
        width: 90%;
        border: solid #FFFFFF;
    }

    .message-input Button {
        width: 10%;
    }

    .message-item {
        padding-top: 1;
    }

    Markdown {
        padding: 1;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+d", "stop", "Stop"),
    ]

    def __init__(
            self,
            meeting: Meeting,
            default_message_to: str | list[str] = "all",
    ):
        super().__init__()
        self.meeting = meeting
        self._meeting_task = None
        self._process_messages_task = None
        self._ui_task = None
        self.default_message_to = default_message_to

    def compose(self) -> ComposeResult:
        yield Vertical(
            Header(name="Pantheon Meeting", id="header"),
            VerticalScroll(id="messages", classes="message-display"),
            Horizontal(
                Input(placeholder="Type your message here...", id="message_input"),
                Button(label="Send", id="send_button"),
                classes="message-input",
            ),
        )

    def action_quit(self) -> None:
        print("Quitting...")
        if self._meeting_task:
            self._meeting_task.cancel()
        if self._process_messages_task:
            self._process_messages_task.cancel()
        if self._ui_task:
            self._ui_task.cancel()

    async def action_stop(self) -> None:
        print("Stopping...")
        msg = Static(
            "Stopping...",
            classes="message-item"
        )
        self.msg_container.mount(msg)
        await self.meeting.stop()
        msg = Static(
            "Stopped. Press [bold]'ctrl+q'[/bold] to exit.",
            classes="message-item"
        )
        self.msg_container.mount(msg)

    async def on_mount(self) -> None:
        self.msg_container = self.query_one("#messages", VerticalScroll)
        self.message_input = self.query_one("#message_input", Input)
        await self.print_greeting()

    def send_message(self) -> None:
        """Handles sending messages."""
        message = self.message_input.value.strip()
        if message:
            if message.startswith("@"):
                target = message[1:].split()[0]
                content = message[len(target)+1:].strip()
                msg = Message(content=content, targets=[target])
            else:
                msg = Message(content=message, targets=self.default_message_to)
            self.meeting.public_queue.put_nowait(
                message_to_record(msg, "user")
            )
            self.message_input.value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send_button":
            self.send_message()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "message_input":
            self.send_message()

    async def print_greeting(self):
        agents_str = ""
        for agent in self.meeting.agents.values():
            agents_str += f"  - [blue]{agent.name}[/blue]\n"
            agents_str += f"    - [green]Instructions:[/green] {agent.instructions}\n"
            if agent.functions:
                agents_str += f"    - [green]Tools:[/green]\n"
                for func in agent.functions.values():
                    agents_str += f"      - {func.__name__}\n"
        agents_str += "\n"

        self.msg_container.mount(Static(
            "[bold]Welcome to the Pantheon Meeting![/bold]\n" +
            "You can start by typing a message or 'ctrl+q' to exit.\n\n" +
            "[bold]Current agents:[/bold]\n" +
            agents_str +
            f"You will send messages to {self.default_message_to} by default. " +
            "If you want to send a message to a specific agent, " +
            "you can @ the target agent's name at the beginning of your message.\n"
        ))
        self.msg_container.scroll_end()

    async def process_messages(self):
        while True:
            event = await self.meeting.stream_queue.get()
            if isinstance(event, Record):
                mesg_head = Static(
                    "[bold]"
                    f"[blue]{event.source}[/blue] "
                    f"[yellow]({event.timestamp})[/yellow] "
                    f"{event.targets} "
                    "[/bold]:",
                    classes="message-item",
                )
                #self.msg_container.mount(mesg_head)
                content = Markdown(event.content)
                self.call_after_refresh(self.msg_container.mount, mesg_head, content)
                self.call_after_refresh(self.msg_container.scroll_end)
                self.refresh()
            elif isinstance(event, ToolEvent):
                if event.tool_name == "send_message":
                    continue
                new_item = Static(
                    f"[bold][red]INFO:[/red][/bold] "
                    f"Agent [blue]{event.agent_name}[/blue] is using tool "
                    f"[green]{event.tool_name}[/green] with arguments "
                    f"[yellow]{event.tool_args_info}[/yellow]",
                    classes="message-item",
                )
                self.msg_container.mount(new_item)
                self.msg_container.scroll_end()
            elif isinstance(event, ToolResponseEvent):
                if event.tool_name == "send_message":
                    continue
                new_item = Static(
                    f"[bold][red]INFO:[/red][/bold] "
                    f"Agent [blue]{event.agent_name}[/blue] got result from tool "
                    f"[green]{event.tool_name}[/green]: "
                    f"[yellow]{event.tool_response}[/yellow]\n",
                    classes="message-item",
                )
                self.msg_container.mount(new_item)
                self.msg_container.scroll_end()
            elif isinstance(event, ThinkingEvent):
                new_item = Static(
                    f"[bold][red]INFO:[/red][/bold] "
                    f"Agent [blue]{event.agent_name}[/blue] is thinking...\n",
                    classes="message-item",
                )
                self.msg_container.mount(new_item)
                self.msg_container.scroll_end()

            self.refresh()

    async def run(self):
        import logging
        logging.getLogger().setLevel(logging.WARNING)
        self._ui_task = asyncio.create_task(self.run_async())
        self._meeting_task = asyncio.create_task(self.meeting.run())
        self._process_messages_task = asyncio.create_task(self.process_messages())

        await asyncio.gather(
            self._ui_task,
            self._meeting_task,
            self._process_messages_task,
        )
