"""Real native App, Agent/Team model loop and caller-owned filesystem tools.

Only the upstream LLM is a deterministic HTTP/SSE server. No real provider is
billed. The caller has no Agent imports/credentials in the runtime protocol.
"""
import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace

import pytest

from test_agent_executions import FUNCTION
from test_agent_native_process import native_process, request


@pytest.fixture
def tool_model():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/responses'):
                self.send_response(404); self.end_headers()
                return
            calls.append((dict(self.headers), body))
            completed = [msg for msg in body['messages'] if msg['role'] == 'tool']
            tools = {tool['function']['name'] for tool in body['tools']}
            assert {'files__edit', 'checks__score'} <= tools
            if len(completed) < 2:
                name = 'files__edit' if not completed else 'checks__score'
                args = {'text': 'x = 7\n'} if not completed else {}
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call-{len(completed)}',
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                finish = 'tool_calls'
            else:
                assert 'score' in str(completed[-1]['content']) and '7' in str(completed[-1]['content'])
                delta, finish = {'role': 'assistant', 'content': 'Verified score 7'}, 'stop'
            events = [
                {'id': 'chat_fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'],
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'chat_fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'],
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}},
            ]
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            for event in events:
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', calls=calls)
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.asyncio
async def test_native_agent_executes_multiple_tool_rounds_and_recovers_result_after_restart(tmp_path, tool_model):
    spec = {'prompt': 'Edit the file, then score it, then report the result.',
            'instructions': 'Use files and checks to finish the task.', 'model': 'openai/gpt-4o-mini',
            'max_turns': 8, 'timeout_seconds': 30,
            'tools': {'files': [FUNCTION], 'checks': [{'name': 'score', 'description': 'Evaluate the saved code',
                'parameters': {'type': 'object', 'properties': {}}}]}}
    mutations = []
    for cycle in range(2):
        with native_process(tmp_path, tool_model.url) as (child, base):
            async with asyncio.timeout(15):
                while True:
                    try:
                        health = await request(base, '/health')
                        if health['ready']: break
                    except OSError:
                        assert child.poll() is None, (tmp_path / 'process.log').read_text()[-15000:]
                    await asyncio.sleep(.05)
            assert 'agent_execution_submit' in health['methods']
            async def rpc(method, **args):
                envelope = await request(base, '/rpc', {'method': 'agent_execution_' + method,
                    'args': {'consumer_id': 'evolution-worker', 'execution_id': 'mutation-1', **args}})
                assert envelope['success'], envelope
                return envelope['result']
            submitted = await rpc('submit', specification=spec)
            await rpc('submit', specification=spec)  # Lost-submit-response recovery.
            if cycle:
                assert submitted['state'] == 'completed'
            async with asyncio.timeout(40):
                while True:
                    status = await rpc('poll')
                    if status['call']:
                        call = status['call']
                        claimed = await rpc('claim', call_id=call['id'], worker_id='owned-worker')
                        assert claimed['claimed'] and not claimed['recovered']
                        if call['provider'] == 'files':
                            (tmp_path / 'owned.py').write_text(call['args']['text'])
                            mutations.append('edit')
                            value = {'saved': True}
                        else:
                            value = {'score': int((tmp_path / 'owned.py').read_text().split('=')[1])}
                            mutations.append('evaluate')
                        reply = dict(call_id=call['id'], worker_id='owned-worker', response={'ok': True, 'value': value})
                        await rpc('reply', **reply)
                        await rpc('reply', **reply)  # Lost-reply-response recovery.
                    if status['state'] not in {'running', 'cancelling'}: break
                    await asyncio.sleep(.01)
            assert status['state'] == 'completed', (status, (tmp_path / 'process.log').read_text()[-15000:])
            response = await rpc('read_result')
            parsed = json.loads(base64.b64decode(response['data']))
            assert parsed['content'] == 'Verified score 7'
            assert len([msg for msg in parsed['details']['messages'] if msg['role'] == 'tool']) == 2
            assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert mutations == ['edit', 'evaluate']
    assert len(tool_model.calls) == 3
    assert all(headers['Authorization'] == 'Bearer process-fixture' for headers, _ in tool_model.calls)
