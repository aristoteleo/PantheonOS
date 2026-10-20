"""Interactive terminal frontend for a bound Pantheon-Agent App.

No backend runtime, local shell execution, settings discovery or direct access
into App-owned data. All commands use the same capability as the GUI.
"""
import asyncio
import json
import os
import stat
import sys

from pantheon.agent_client import AgentClientError, stream_turn


HELP = '''Commands:
  /chats                 List this App's conversations
  /resume ID|NAME|INDEX   Switch conversation (empty selects most recent)
  /new                   Create a conversation
  /history               Show saved messages
  /models                List configured models
  /model MODEL           Select a model for the first Agent
  /agents                Show the current team
  /agent NAME            Select the active Agent
  /stop                  Request the current conversation to stop
  /help                  Show these commands
  /quit                  Drain Apps and exit
'''


class TerminalRenderer:
    """Text rendering with per-turn bounded bookkeeping; no backend file reads."""
    def __init__(self, console):
        self.console = console
        self.streamed = set()
        self.tools = set()

    def text(self, value, **kwargs):
        self.console.print(value, markup=False, highlight=False, **kwargs)

    async def event(self, event):
        data, kind = event['data'], event['type']
        if kind == 'chunk':
            chunk = data['chunk']
            content = chunk.get('content')
            if isinstance(content, str) and content:
                self.text(content, end='')
                identity = chunk.get('message_id')
                if isinstance(identity, str): self.streamed.add(identity)
        elif kind == 'tool_delta':
            name = data.get('tool_name')
            if isinstance(name, str) and name not in self.tools:
                self.text('\nUsing ' + name)
                self.tools.add(name)
        elif kind == 'step':
            message = data.get('step_message', {})
            if not isinstance(message, dict): return
            content = message.get('content')
            identity = message.get('id')
            if message.get('role') == 'tool':
                self.text('\nTool result: ' + (content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)))
            elif message.get('role') == 'assistant' and (not isinstance(identity, str) or identity not in self.streamed) and isinstance(content, str):
                self.text(content)
            if isinstance(identity, str): self.streamed.discard(identity)
        elif kind == 'chat_finished':
            self.text('')
            if data.get('status') in ('error', 'interrupted'):
                self.text('Run ' + data['status'] + '; inspect /history.')
            self.streamed.clear()
            self.tools.clear()

    async def reset(self, history):
        # An append-only terminal cannot erase an old partial response reliably.
        # Label the authoritative replacement instead of silently appending it as
        # a second response; tools are never re-executed during rendering.
        self.text('\nStream gap: showing the saved conversation again.')
        self.streamed.clear()
        self.tools.clear()
        for message in history['messages']:
            self.text(json.dumps(message, ensure_ascii=False))
        for item in history['inflight']:
            await self.event(item)


async def choose_model(client, chat_id, model):
    agents = (await client.call('get_agents', chat_id=chat_id)).get('agents')
    if not isinstance(agents, list) or not agents or not isinstance(agents[0].get('name'), str):
        raise AgentClientError('No Agent is available for model selection')
    await client.call('set_agent_model', chat_id=chat_id, agent_name=agents[0]['name'], model=model)


class TerminalInput:
    """Cancellable input, including pipes, without a blocking executor thread."""
    async def __aenter__(self):
        self.transport = None
        self.file = None
        if sys.stdin.isatty():
            from prompt_toolkit import PromptSession
            self.prompt = PromptSession()
        else:
            reader = asyncio.StreamReader(limit=128 * 1024)
            protocol = asyncio.StreamReaderProtocol(reader)
            pipe = os.fdopen(os.dup(sys.stdin.fileno()), 'rb', buffering=0)
            if stat.S_ISREG(os.fstat(pipe.fileno()).st_mode):
                self.file = pipe
                return self
            try:
                self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, pipe)
            except BaseException:
                pipe.close()
                raise
            self.reader = reader
        return self

    async def read(self):
        if self.file is not None:
            # Regular files cannot be registered with a selector. A bounded
            # local read has no indefinitely blocked stdin thread to abandon.
            data = self.file.readline(128 * 1024 + 1)
        elif self.transport is None:
            return await self.prompt.prompt_async('You > ')
        else:
            try:
                data = await self.reader.readline()
            except ValueError:
                raise AgentClientError('Terminal input exceeds the 128 KiB line limit') from None
        if len(data) > 128 * 1024:
            raise AgentClientError('Terminal input exceeds the 128 KiB line limit')
        if not data: raise EOFError
        try:
            return data.decode('utf-8')
        except UnicodeError:
            raise AgentClientError('Terminal input must be UTF-8') from None

    async def __aexit__(self, *args):
        if self.transport is not None: self.transport.close()
        if self.file is not None: self.file.close()


async def run_interactive(client, *, chat_id=None, resume=False, template=None, model=None,
                          read_line=None, console=None):
    from rich.console import Console
    if console is None: console = Console()
    if read_line is None:
        async with TerminalInput() as source:
            return await run_interactive(client, chat_id=chat_id, resume=resume, template=template,
                                         model=model, read_line=source.read, console=console)
    info = await client.negotiate()
    if type(info.get('event_cursor_protocol')) is not int or info['event_cursor_protocol'] != 1:
        raise AgentClientError('Update this Agent App for the streaming terminal frontend')
    selected = await client.conversation(chat_id=chat_id, resume=resume, template=template)
    if model is not None: await choose_model(client, selected, model)
    output = TerminalRenderer(console)
    output.text('Pantheon-Agent · ' + selected + '\n/help lists commands; /quit closes this profile.')
    while True:
        try:
            text = (await read_line()).strip()
        except EOFError:
            return
        except KeyboardInterrupt:
            output.text('Use /quit or Ctrl-D to close this profile.')
            continue
        if not text: continue
        try:
            if text.startswith('/'):
                command, _, argument = text.partition(' ')
                argument = argument.strip()
                if command in ('/quit', '/exit'): return
                if command == '/help': output.text(HELP)
                elif command == '/new':
                    selected = await client.conversation()
                    output.text('Conversation: ' + selected)
                elif command == '/resume':
                    selected = await client.conversation(resume=argument or True)
                    output.text('Conversation: ' + selected)
                elif command == '/chats':
                    output.text(json.dumps(await client.call('list_chats'), indent=2, ensure_ascii=False))
                elif command == '/history':
                    for message in (await client.history(selected))['messages']:
                        output.text(json.dumps(message, ensure_ascii=False))
                elif command == '/models':
                    output.text(json.dumps(await client.call('list_available_models'), indent=2, ensure_ascii=False))
                elif command == '/model' and argument:
                    await choose_model(client, selected, argument)
                    output.text('Model selected.')
                elif command == '/agents':
                    output.text(json.dumps(await client.call('get_agents', chat_id=selected), indent=2, ensure_ascii=False))
                elif command == '/agent' and argument:
                    await client.call('set_active_agent', chat_id=selected, agent_name=argument)
                elif command == '/stop':
                    await client.stop(selected)
                    output.text('Stop requested. The backend is responsible for saving and draining.')
                else:
                    output.text('Unknown or incomplete command. Use /help; no prompt was submitted.')
                continue
            await stream_turn(client, selected, text, output.event, on_reset=output.reset)
        except AgentClientError as error:
            output.text(str(error))
        except KeyboardInterrupt:
            output.text('\nTurn interrupted. Inspect /history before continuing.')


async def run_json_stream(client, message, *, chat_id=None, resume=False, template=None, model=None):
    """Machine-readable event/reset/result lines, without presentation truncation."""
    info = await client.negotiate()
    if type(info.get('event_cursor_protocol')) is not int or info['event_cursor_protocol'] != 1:
        raise AgentClientError('Update this Agent App for streaming terminal calls')
    selected = await client.conversation(chat_id=chat_id, resume=resume, template=template)
    if model is not None: await choose_model(client, selected, model)
    async def event(value): print(json.dumps({'kind': 'event', 'event': value}), flush=True)
    async def reset(value): print(json.dumps({'kind': 'history_reset', 'history': value}), flush=True)
    result = await stream_turn(client, selected, message, event, on_reset=reset)
    print(json.dumps({'kind': 'result', **result}), flush=True)
