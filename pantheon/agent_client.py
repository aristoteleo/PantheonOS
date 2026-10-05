"""Agent App frontend client over an explicitly bound ordinary RPC capability.

This module imports no Agent runtime, REPL internals or ambient settings. Calls
never discover a replacement deployment or automatically repeat a mutation.
"""
from datetime import datetime
import hashlib
import json


class AgentClientError(RuntimeError):
    pass


def select_resume(chats, selection):
    def activity(chat):
        try:
            return datetime.fromisoformat(chat.get('last_activity_date')).timestamp()
        except (TypeError, ValueError, OverflowError):
            return float('-inf')
    rows = sorted(chats, key=activity, reverse=True)
    if not rows:
        raise AgentClientError('No chat sessions found in this Agent App')
    if selection is True:
        return rows[0]['id']
    value = str(selection)
    if value.isdigit() and 1 <= int(value) <= len(rows):
        return rows[int(value) - 1]['id']
    for row in rows:
        if row['id'].startswith(value) or str(row.get('name') or '').lower().startswith(value.lower()):
            return row['id']
    raise AgentClientError('Chat not found in this Agent App')


class AgentAppClient:
    def __init__(self, invoke):
        self.invoke = invoke

    async def call(self, method, *, timeout=30, **arguments):
        try:
            result = await self.invoke(method, arguments, timeout)
        except Exception:
            raise AgentClientError(
                'Agent RPC outcome is unknown. Inspect this conversation before repeating a mutation.') from None
        if not isinstance(result, dict):
            raise AgentClientError('Agent returned an invalid response')
        if result.get('success') is False:
            raise AgentClientError('Agent operation failed; inspect this App and its conversation')
        return result

    async def negotiate(self):
        info = await self.call('get_agent_app_info')
        if any(type(info.get(k)) is not int or info[k] != 1
               for k in ('protocol', 'history_protocol', 'event_protocol')):
            raise AgentClientError('This Agent App does not support the native client protocol')
        return info

    async def conversation(self, *, chat_id=None, resume=False, template=None):
        if chat_id is not None and resume is not False:
            raise AgentClientError('Choose either a chat ID or a resume selection')
        if chat_id is not None or resume is not False:
            rows = (await self.call('list_chats')).get('chats')
            if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows):
                raise AgentClientError('Agent returned an invalid conversation list')
            if chat_id is not None:
                if not any(row['id'] == chat_id for row in rows):
                    raise AgentClientError('Chat not found in this Agent App')
            else:
                chat_id = select_resume(rows, resume)
            if template is not None:
                raise AgentClientError('Apply template changes explicitly before resuming this conversation')
            return chat_id
        arguments = {'chat_name': 'repl-session'}
        if template is not None:
            if not isinstance(template, dict): raise AgentClientError('Supply a JSON team template object')
            arguments['template_obj'] = template
        result = await self.call('create_chat', **arguments)
        identity = result.get('chat_id')
        if not isinstance(identity, str) or not identity:
            raise AgentClientError('Agent did not return its conversation identity; do not repeat creation automatically')
        return identity

    async def is_running(self, chat_id):
        # Status needs only metadata. Do not download a possibly huge history
        # just to send another prompt; backend memory remains authoritative.
        rows = (await self.call('list_chats')).get('chats')
        if not isinstance(rows, list):
            raise AgentClientError('Invalid conversation status')
        matches = [row for row in rows if isinstance(row, dict) and row.get('id') == chat_id]
        if len(matches) != 1 or type(matches[0].get('running')) is not bool:
            raise AgentClientError('Conversation status is unavailable')
        return matches[0]['running']

    async def history(self, chat_id, *, max_bytes=64 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('Supply a positive history size limit')
        snapshot = await self.call('open_agent_history', chat_id=chat_id)
        identity = snapshot.get('snapshot_id')
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise AgentClientError('Invalid history snapshot identity')
        try:
            if (type(snapshot.get('protocol')) is not int or snapshot['protocol'] != 1 or snapshot.get('chat_id') != chat_id
                    or type(snapshot.get('size')) is not int or not 0 < snapshot['size'] <= max_bytes
                    or type(snapshot.get('parts')) is not int or not 0 < snapshot['parts'] <= snapshot['size']
                    or type(snapshot.get('total')) is not int or snapshot['total'] < 0):
                raise AgentClientError('History snapshot is invalid or exceeds the requested size limit')
            data = bytearray()
            for part in range(snapshot['parts']):
                result = await self.call('read_agent_history', chat_id=chat_id, snapshot_id=identity, part=part)
                if (type(result.get('protocol')) is not int or result['protocol'] != 1 or result.get('chat_id') != chat_id
                        or result.get('snapshot_id') != identity or type(result.get('part')) is not int or result['part'] != part
                        or type(result.get('parts')) is not int or result['parts'] != snapshot['parts'] or not isinstance(result.get('json_fragment'), str)):
                    raise AgentClientError('History part does not belong to this snapshot')
                try:
                    fragment = result['json_fragment'].encode('ascii')
                except UnicodeEncodeError:
                    raise AgentClientError('Invalid history encoding') from None
                if not fragment or len(data) + len(fragment) > snapshot['size']:
                    raise AgentClientError('History size does not match its snapshot')
                data.extend(fragment)
            if len(data) != snapshot['size'] or hashlib.sha256(data).hexdigest() != snapshot.get('sha256'):
                raise AgentClientError('History digest does not match its snapshot')
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                raise AgentClientError('Invalid history JSON') from None
            if (not isinstance(value, dict) or not isinstance(value.get('messages'), list)
                    or type(value.get('total')) is not int or value['total'] != snapshot['total'] or len(value['messages']) != snapshot['total']
                    or not isinstance(value.get('inflight'), list) or type(value.get('running')) is not bool):
                raise AgentClientError('History content does not match its snapshot')
            return value
        finally:
            await self.call('release_agent_history', chat_id=chat_id, snapshot_id=identity)

    async def send(self, chat_id, message, *, timeout=600):
        if not isinstance(message, str) or not message.strip():
            raise AgentClientError('Supply a nonempty prompt')
        return await self.call('chat', timeout=timeout, chat_id=chat_id,
                               message=[{'role': 'user', 'content': message}])

    async def stop(self, chat_id):
        # The backend owns draining and saving. This acknowledgement is not proof
        # the running call has finished; its observer still has to join it.
        return await self.call('stop_chat', chat_id=chat_id)


async def run_once(client, message, *, chat_id=None, resume=False, template=None, model=None):
    """One terminal turn against the already running App, with no local backend."""
    import asyncio
    await client.negotiate()
    selected = await client.conversation(chat_id=chat_id, resume=resume, template=template)
    if await client.is_running(selected):
        raise AgentClientError('This conversation is running; wait or stop it explicitly before a one-shot call')
    if model is not None:
        agents = (await client.call('get_agents', chat_id=selected)).get('agents')
        if not isinstance(agents, list) or not agents or not isinstance(agents[0].get('name'), str):
            raise AgentClientError('No Agent is available for the requested model')
        await client.call('set_agent_model', chat_id=selected, agent_name=agents[0]['name'], model=model)
    try:
        result = await client.send(selected, message)
    except asyncio.CancelledError:
        # RPC cancellation alone cannot cancel an admitted backend mutation.
        # Request stop explicitly; the profile host then drains the App's calls
        # and saves before closing its infrastructure.
        pending = asyncio.create_task(client.stop(selected))
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
        # Retrieve the result, including an uncertain stop, before propagating
        # cancellation. The profile's ordinary stop still owns final draining.
        await asyncio.gather(pending, return_exceptions=True)
        raise
    if result.get('queued'):
        raise AgentClientError('The message was queued by a concurrent run; inspect the conversation before sending it again')
    if result.get('chat_id') != selected or not isinstance(result.get('response'), str):
        raise AgentClientError('The turn outcome is incomplete; inspect the conversation before repeating it')
    return {'chat_id': selected, 'response': result['response']}
