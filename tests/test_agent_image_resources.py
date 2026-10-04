"""Files capability → model wire payload, preserving stored references."""
import asyncio
import base64
import copy
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from pantheon.agent import Agent
from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.factory.bindings import _bindings_from_spec
from pantheon.factory.instances import AgentInstanceBinding, AgentInstanceFactory, config_revision
from pantheon.utils.image_resources import BoundImageResolver, expand_bound_images
from pantheon.utils.vision import ImageStore, expand_image_references_for_llm
from test_agent_gui_files import files_provider, tls_material
from test_agent_model_scope import endpoint as model_endpoint, scopes


def message(url, role='user'):
    return {'role': role, 'content': [{'type': 'text', 'text': 'Describe this image'},
        {'type': 'image_url', 'image_url': {'url': url, 'detail': 'high'}}]}


def pixels(uri):
    with Image.open(io.BytesIO(base64.b64decode(uri.split(',', 1)[1]))) as image:
        return image.size, image.convert('RGBA').getpixel((0, 0))


@pytest.mark.asyncio
async def test_instance_files_images_reach_model_and_never_read_agent_decoys(
        tmp_path, files_provider, tls_material, model_endpoint, scopes):
    scope = scopes({'OPENAI_API_KEY': 'image-fixture', 'OPENAI_API_BASE': model_endpoint.url+'/byok/v1'})
    configuration = RuntimeConfiguration({}, {
        name: RuntimeCredential(**credential) for name, credential in files_provider.credentials.items()
    }, 'agent', 'c'*64, 1, 'backend')
    bindings = _bindings_from_spec(configuration, {'toolsets': {
        'file_manager': files_provider.bindings['file_manager']}}, tls_material.tls)
    config = {'name':'Reader','instructions':'Describe images', 'model':'openai/gpt-5.6-terra',
              'icon':'', 'toolsets':['file_manager']}
    binding = AgentInstanceBinding('10000000-0000-4000-8000-000000000001', 'chat', 'reader',
                                   config_revision(config), bindings)
    factory = AgentInstanceFactory([binding], model_scope=scope)
    try:
        agent = (await factory({'reader':config}, conversation_id='chat'))[0]
        original = [message('file://preview.png')]
        frozen = copy.deepcopy(original)
        await agent._run_stream(original, tool_use=False)
        assert original == frozen
        request = model_endpoint.requests[-1][2]
        image = next(block['image_url']['url'] if isinstance(block['image_url'], dict) else block['image_url']
                     for row in request.get('messages', request.get('input', []))
                     if isinstance(row.get('content'), list) for block in row['content']
                     if block.get('type') in ('image_url', 'input_image'))
        assert pixels(image) == ((1568, 784), (255, 0, 0, 255))
        assert any(method == 'fetch_image_base64' and args['image_path'] == 'preview.png'
                   for _, method, args in files_provider.calls)
        # A real file on the Agent host outside the provider workspace is not
        # an authorized substitute. No inference or fallback model may run.
        decoy = tmp_path/'agent-only.png'
        Image.new('RGB', (4, 4), 'blue').save(decoy)
        count = len(model_endpoint.requests)
        for path in (decoy.as_uri(), 'file://missing.png'):
            with pytest.raises(ValueError, match='bound Files service'):
                await agent._run_stream([message(path)], tool_use=False)
        assert len(model_endpoint.requests) == count
        await bindings.toolsets['file_manager'].shutdown()
        with pytest.raises(ValueError, match='bound Files service'):
            await agent._run_stream(original, tool_use=False)
        assert len(model_endpoint.requests) == count
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_uploaded_owned_images_and_symlink_escape_never_use_files(tmp_path, scopes):
    scope = scopes()
    store = ImageStore(scope.settings.pantheon_dir/'images')
    buffer = io.BytesIO()
    Image.new('RGBA', (8, 4), 'red').save(buffer, format='PNG')
    data = 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
    uploaded = message(data)
    store.process_message_images(uploaded, 'chat', normalize_paths=False)
    reference = uploaded['content'][1]['image_url']['url']
    files = SimpleNamespace(call_tool=AsyncMock(side_effect=AssertionError('Must not fall through')))
    resolver = BoundImageResolver(image_root=store.storage_root, files=files)
    restored = await expand_bound_images([uploaded], resolver)
    assert pixels(restored[0]['content'][1]['image_url']['url']) == ((8, 4), (255, 0, 0, 255))
    assert uploaded['content'][1]['image_url']['url'] == reference
    outside = tmp_path/'outside.png'
    outside.write_bytes(buffer.getvalue())
    link = store.storage_root/'escape.png'
    link.symlink_to(outside)
    for path in (link, store.storage_root/'missing.png', store.storage_root/'..'/'outside.png'):
        with pytest.raises(ValueError, match='Agent image store'):
            await resolver('file://'+str(path))
    files.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_each_model_round_expands_new_tool_images_without_persisting_bytes(scopes):
    resolve = AsyncMock(return_value='data:image/png;base64,aGVsbG8=')
    agent = Agent('Scoped', '', model='fleet-model://service/model', model_scope=scopes(), image_resolver=resolve)
    agent._acompletion = AsyncMock(return_value={'role':'assistant','content':'done'})
    history = [message('file:///remote/same.png')]
    for _ in range(2):
        await agent._acompletion_with_models(history, False, None, None, False)
        history.append(message('file:///remote/new.png', role='tool'))
    assert resolve.call_count == 3  # per-request dedup, never across authorization checks
    forwarded = agent._acompletion.call_args.args[0]
    assert forwarded[-1]['content'][1]['image_url']['url'].startswith('data:image/')
    assert all(row['content'][1]['image_url']['url'].startswith('file://') for row in history)


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    {'success':False, 'error':'private upstream message'},
    {'success':True, 'data_uri':'file:///secret.png'},
    {'success':True, 'data_uri':'https://private/image.png'},
    {'success':True, 'data_uri':'data:image/png;base64,not-valid'},
])
async def test_invalid_provider_images_stop_before_adapter_local_or_network_reads(response):
    resolver = BoundImageResolver(files=SimpleNamespace(call_tool=AsyncMock(return_value=response)))
    with pytest.raises(ValueError, match='bound Files service') as error:
        await expand_bound_images([message('file:///remote.png')], resolver)
    assert 'private' not in str(error.value) and 'secret' not in str(error.value)


@pytest.mark.asyncio
async def test_cancellation_propagates_and_does_not_cache_partial_requests():
    files = SimpleNamespace(call_tool=AsyncMock(side_effect=asyncio.CancelledError))
    resolver = BoundImageResolver(files=files)
    original = [message('file:///remote.png')]
    with pytest.raises(asyncio.CancelledError):
        await expand_bound_images(original, resolver)
    assert original[0]['content'][1]['image_url']['url'] == 'file:///remote.png'


def test_legacy_local_expansion_remains_available(tmp_path):
    path = tmp_path/'local.png'
    Image.new('RGBA', (8, 4), 'red').save(path)
    original = [message(path.as_uri())]
    assert pixels(expand_image_references_for_llm(original)[0]['content'][1]['image_url']['url']) == (
        (8, 4), (255, 0, 0, 255))
    assert original[0]['content'][1]['image_url']['url'] == path.as_uri()


@pytest.mark.asyncio
async def test_vision_companion_uses_only_current_scope_and_private_description_cache(scopes, monkeypatch):
    from pantheon.utils import llm, vision_downgrade
    from pantheon.utils.model_selector import ModelSelector
    first, second = scopes(), scopes()
    first.model_info = second.model_info = lambda model: {'supports_vision': False}
    selected = []
    def candidates(selector, *args):
        selected.append(selector._scope)
        return ['openai/scoped-vision']
    monkeypatch.setattr(ModelSelector, 'find_vision_models', candidates)
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        assert kwargs['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='scope-specific description'))])
    monkeypatch.setattr(llm, 'acompletion', complete)
    for scope in (first, first, second):
        output = await vision_downgrade.downgrade_blind_user_images(
            [message('data:image/png;base64,aGVsbG8=')], 'openai/text-only', scope=scope)
        assert 'scope-specific description' in output[0]['content'][1]['text']
    assert selected == [first, second]
    assert [call['scope'] for call in calls] == [first, second]
    assert len(first.vision_descriptions) == len(second.vision_descriptions) == 1
