"""Packaged Notebook pixels survive Agent storage and original Model Services."""
import asyncio
import base64
import copy
from http.server import BaseHTTPRequestHandler
import io
import json
from pathlib import Path
import time

import httpx
from PIL import Image
import pytest

from pantheon.agent import Agent
from pantheon.models.client import ModelServices, model_ref
from pantheon.models.messages import prepare_chat_completion_messages
from notebook_app_fixture import notebook_rpc
from test_agent_model_scope import scopes
from test_model_services import connector_module, deployment, serve


def pixel_uri(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (8, 5), color).save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()


def image(uri):
    return {'type': 'image_url', 'image_url': {'url': uri, 'detail': 'high'}}


def test_tool_images_preserve_parallel_call_order_and_do_not_mutate_history():
    history = [
        {'role': 'assistant', 'tool_calls': [{'id': 'one'}, {'id': 'two'}, {'id': 'three'}]},
        {'role': 'tool', 'tool_call_id': 'one', 'raw_content': 'private', 'content': [
            {'type': 'text', 'text': 'Plot one'}, image(pixel_uri())]},
        {'role': 'tool', 'tool_call_id': 'two', 'content': 'No image'},
        {'role': 'tool', 'tool_call_id': 'three', 'content': [image(pixel_uri('blue'))]},
        {'role': 'assistant', 'content': 'Analysis'},
        {'role': 'user', 'content': 'Continue'},
    ]
    original = copy.deepcopy(history)
    result = prepare_chat_completion_messages(history)
    assert history == original
    assert [m['role'] for m in result] == ['assistant', 'tool', 'tool', 'tool', 'user', 'assistant', 'user']
    assert [m['tool_call_id'] for m in result if m['role'] == 'tool'] == ['one', 'two', 'three']
    assert all(isinstance(m['content'], str) for m in result if m['role'] == 'tool')
    assert 'raw_content' not in result[1]
    attachments = result[4]['content']
    assert [p['image_url']['url'] for p in attachments if p['type'] == 'image_url'] == [pixel_uri(), pixel_uri('blue')]
    assert 'untrusted tool output' in attachments[0]['text']
    assert any('one' in p.get('text', '') for p in attachments)
    assert any('three' in p.get('text', '') for p in attachments)
    assert prepare_chat_completion_messages(result) == result, 'Normalization must not duplicate images on reuse'


@pytest.mark.parametrize('url', ['file:///private/secret.png', '/tmp/plot.png', 'other-scheme:secret', None])
def test_wire_conversion_never_reads_unresolved_tool_paths(url):
    with pytest.raises(ValueError, match='owning App'):
        prepare_chat_completion_messages([{'role': 'tool', 'tool_call_id': 'one', 'content': [image(url)]}])


def test_missing_tool_identity_is_not_relabelled_as_user_input():
    with pytest.raises(ValueError, match='tool_call_id'):
        prepare_chat_completion_messages([{'role': 'tool', 'content': [image(pixel_uri())]}])


@pytest.mark.asyncio
@pytest.mark.parametrize('vision', [None, False])
async def test_unconfirmed_vision_never_sends_images_to_a_fallback(vision):
    row, calls = deployment(), []
    row['models'][0]['vision'] = vision
    async def directory(request):
        calls.append(request.url.path)
        assert request.url.path == '/api/model-services'
        return httpx.Response(200, json={'deployments': [row]})
    client = ModelServices('https://hub.test', 'fixture', httpx.MockTransport(directory))
    try:
        with pytest.raises(ValueError, match='Vision support'):
            await client.complete(model_ref('mac', 'example:8b'), [
                {'role': 'tool', 'tool_call_id': 'one', 'content': [image(pixel_uri())]}])
        assert calls == ['/api/model-services']
    finally:
        await client.aclose()




@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['add', 'update', 'execute', 'desktop'])
async def test_tool_pixels_reach_model_connector_after_agent_storage(request, scopes, tmp_path, operation):
    received = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            event = {'choices': [{'index': 0, 'delta': {'content': 'PLOT_RECEIVED'}, 'finish_reason': 'stop'}]}
            self.wfile.write(('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode())

    if operation != 'desktop':
        notebook_rpc = request.getfixturevalue('notebook_rpc')
        assert notebook_rpc('create_notebook', notebook_path='plot.ipynb')['success']
    uri = pixel_uri()
    code = f"from IPython.display import Image,display\nimport base64\ndisplay(Image(base64.b64decode({uri.split(',', 1)[1]!r})))"
    cell = None
    if operation not in ('add', 'desktop'):
        cell = notebook_rpc('add_cell', notebook_path='plot.ipynb', content=code if operation == 'execute' else 'pass')['cell_id']
    connector = connector_module.Connector(tmp_path / 'connector')
    with serve(Engine) as engine:
        connector.configure({'engine': 'ollama', 'endpoint': engine})
        row = deployment()
        row['config_revision'] = connector.revision
        row['models'][0]['context'] = 8192
        with serve(connector_module.handler(connector)) as endpoint:
            network = httpx.AsyncHTTPTransport()
            async def route(request):
                if request.url.host == 'hub.test':
                    if request.url.path == '/api/model-services/routes':
                        return httpx.Response(200, json={'routes': []})
                    if request.url.path == '/api/model-services':
                        return httpx.Response(200, json={'deployments': [row]})
                    assert request.url.path == '/api/fleet/apps/workload-connect'
                    assert json.loads(request.content) == row['binding']
                    return httpx.Response(200, json={'origin': 'https://connector.test', 'access_token': 'fixture', 'expires': time.time()+60})
                assert request.url.host == 'connector.test'
                return await network.handle_async_request(httpx.Request(request.method, endpoint + request.url.path,
                    headers=dict(request.headers), content=request.content))
            client = ModelServices('https://hub.test', 'fixture', httpx.MockTransport(route))
            try:
                await client.catalog()
                ref = model_ref('mac', 'example:8b')
                scope = scopes(fleet_client=client, resolve_models=lambda _: [ref])
                agent = Agent('Image test', 'Inspect the notebook output.', model=ref, model_scope=scope, use_memory=False)
                @agent.tool
                async def plot():
                    """Produce pixels on a separate tool process."""
                    if operation == 'desktop':
                        from desktop_snapshot_fixture import desktop_snapshot
                        return await asyncio.to_thread(desktop_snapshot, tmp_path / 'desktop-workspace', uri)
                    if operation == 'execute':
                        return await asyncio.to_thread(notebook_rpc, 'notebook_execute', notebook_path='plot.ipynb',
                            action='execute', cell_id=cell)
                    return await asyncio.to_thread(notebook_rpc, 'notebook_edit', notebook_path='plot.ipynb',
                        action='add_cell' if operation == 'add' else 'update_cell', cell_id=cell, execute=True, content=code)
                calls = [{'id': 'plot-call', 'type': 'function', 'function': {'name': 'plot', 'arguments': '{}'}}]
                results = await agent._handle_tool_calls(calls, {}, 30)
                assert len(results) == 1
                blocks = results[0]['content']
                stored = [p['image_url']['url'] for p in blocks if p.get('type') == 'image_url']
                assert len(stored) == 1 and stored[0].startswith('file://')
                assert Path(stored[0].removeprefix('file://')).is_relative_to(scope.settings.pantheon_dir / 'images')
                history = [{'role': 'user', 'content': 'Run the plot and inspect its pixels.'},
                    {'role': 'assistant', 'content': '', 'tool_calls': calls}, *results]
                original = copy.deepcopy(history)
                reply = await agent._acompletion_with_models(history, False, None, None, False, model=ref)
                assert reply['content'] == 'PLOT_RECEIVED'
                assert history == original
                assert len(received) == 1
                messages = received[0]['messages']
                tool = next(m for m in messages if m['role'] == 'tool')
                assert tool['tool_call_id'] == 'plot-call' and isinstance(tool['content'], str)
                attached = [p for m in messages if m['role'] == 'user' and isinstance(m.get('content'), list)
                    for p in m['content'] if p.get('type') == 'image_url']
                assert len(attached) == 1
                data = base64.b64decode(attached[0]['image_url']['url'].split(',', 1)[1])
                with Image.open(io.BytesIO(data)) as decoded:
                    assert decoded.size == (8, 5)
                    # The existing owned-image resolver may normalize RGB PNG to JPEG.
                    assert all(abs(a-b) <= 2 for a, b in zip(decoded.convert('RGB').getpixel((0, 0)), (255, 0, 0)))
                assert 'file://' not in json.dumps(received[0])
            finally:
                await client.aclose()
                await network.aclose()


@pytest.mark.asyncio
async def test_large_tool_summary_uses_agent_owned_storage(scopes):
    scope = scopes(resolve_models=lambda _: ['openai/gpt-4o'])
    agent = Agent('Output ownership', '', model='openai/gpt-4o', model_scope=scope, use_memory=False)
    @agent.tool
    async def large_result():
        """Return a large structured tool result."""
        return {'text': 'preserve original output\n' * 4000}
    [result] = await agent._handle_tool_calls([{'id': 'large-call', 'type': 'function',
        'function': {'name': 'large_result', 'arguments': '{}'}}], {}, 5)
    root = scope.settings.tmp_dir / 'tool-results'
    assert '<persisted-output>' in result['content'] and str(root) in result['content']
    assert list(root.rglob('*.json'))
    saved = next(root.rglob('*.json')).read_text()
    assert 'preserve original output' in saved


def test_svg_output_is_valid_data_uri_without_agent_lookup(notebook_rpc):
    assert notebook_rpc('create_notebook', notebook_path='svg.ipynb')['success']
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>'
    result = notebook_rpc('add_cell', notebook_path='svg.ipynb', execute=True,
        content=f'from IPython.display import display,SVG\ndisplay(SVG({svg!r}))')
    assert result['execution']['success']
    uri = result['content_blocks'][0]['image_url']['url']
    assert uri.startswith('data:image/svg+xml;base64,')
    assert '<svg' in base64.b64decode(uri.split(',', 1)[1], validate=True).decode()
    assert result['execution']['content_blocks'] == result['content_blocks']
