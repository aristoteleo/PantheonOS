import asyncio
import sys

from textual.app import App, ComposeResult
from textual.widgets import Header, Static, Input, Button, Markdown
from textual.containers import Vertical, Horizontal, VerticalScroll

from ..agent import Agent
from ..meeting import (
    Meeting, Message, message_to_record,
    ToolEvent, ToolResponseEvent, AgentBeginEvent, Record
)


class Repl(App):
    TITLE = "Pantheum Meeting"

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
    }

    .message-input Button {
        width: 10%;
    }

    Markdown {
        padding: 1;
    }
    """

    def __init__(self, agents: list[Agent]):
        super().__init__()
        self.agents = agents
        self.meeting = Meeting(agents)
        self._meeting_task = None
        self._process_messages_task = None
        self._ui_task = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Header(name="Pantheum Meeting", id="header"),
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

    def on_mount(self) -> None:
        self.msg_container = self.query_one("#messages", VerticalScroll)
        self.message_input = self.query_one("#message_input", Input)

    def send_message(self) -> None:
        """Handles sending messages."""
        message = self.message_input.value.strip()
        if message:
            msg = Message(content=message, targets="all")
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

    async def process_messages(self):
        while True:
            event = await self.meeting.stream_queue.get()
            if isinstance(event, Record):
                new_item = Static(
                    "[bold]"
                    f"[blue]{event.source}[/blue] "
                    f"[yellow]({event.timestamp})[/yellow] "
                    f"{event.targets} "
                    "[/bold]:"
                )
                self.msg_container.mount(new_item)
                content = Markdown(event.content)
                self.call_after_refresh(self.msg_container.mount, content)
                self.msg_container.scroll_end()
            elif isinstance(event, ToolEvent):
                new_item = Static(
                    f"[bold][red]INFO:[/red][/bold] "
                    f"Agent [blue]{event.agent_name}[/blue] is using tool "
                    f"[green]{event.tool_name}[/green] with arguments "
                    f"[yellow]{event.tool_args_info}[/yellow]"
                )
                self.msg_container.mount(new_item)
            elif isinstance(event, ToolResponseEvent):
                new_item = Static(
                    f"[bold][red]INFO:[/red][/bold] "
                    f"Agent [blue]{event.agent_name}[/blue] got result from tool "
                    f"[green]{event.tool_name}[/green]: "
                    f"[yellow]{event.tool_response}[/yellow]\n"
                )
                self.msg_container.mount(new_item)
                self.msg_container.scroll_end()
            elif isinstance(event, AgentBeginEvent):
                new_item = Static(
                    f"[bold][red]INFO:[/red][/bold] "
                    f"Agent [blue]{event.agent_name}[/blue] is thinking...\n"
                )
                self.msg_container.mount(new_item)
                self.msg_container.scroll_end()

    async def run(self):
        import logging
        logging.getLogger().setLevel(logging.WARNING)
        self._meeting_task = asyncio.create_task(self.meeting.run())
        self._process_messages_task = asyncio.create_task(self.process_messages())
        self._ui_task = asyncio.create_task(self.run_async())

        await asyncio.gather(
            self._meeting_task,
            self._process_messages_task,
            self._ui_task,
        )
