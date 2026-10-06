"""Captured images survive import without consulting paths from message prose."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from pantheon.chatroom.migration import inspect_legacy, fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_images import image_destination
from pantheon.utils.image_resources import BoundImageResolver
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_agent_instance_factory import RECIPE
from test_agent_image_resources import pixels


@pytest.fixture
def images(legacy, tmp_path):
    (Path(legacy['project_config']) / 'settings.json').write_text('{}')
    other = tmp_path / 'second-project'
    other.mkdir()
    legacy['projects'].append({'id': 'second', 'name': 'Other', 'path': str(other)})
    stores = [Path(legacy['project_config']) / 'images', Path(legacy['global_config']) / 'images',
              other / '.pantheon/images']
    paths = []
    for store, color in zip(stores, ('red', 'blue', 'green')):
        path = store / 'same-chat/same.png'
        path.parent.mkdir(parents=True)
        Image.new('RGBA', (8, 4), color).save(path)
        paths.append(path)
    blocks = [{'type': 'image_url', 'image_url': {'url': 'file://' + str(path), 'detail': 'high'}} for path in paths]
    external = 'file://' + str(Path(legacy['projects'][0]['path']) / 'external-image.png')
    blocks.extend([{'type':'text', 'text':str(paths[0])},
                   {'type':'image_url', 'image_url':{'url':external}},
                   {'type':'image_url', 'image_url':{'url':'https://example.invalid/image.png'}}])
    message = {'role':'user', 'content':blocks, '_llm_content':copy.deepcopy(blocks),
               'tool_calls':[{'function':{'arguments':json.dumps({'path':str(paths[0])})}}]}
    memory = Path(legacy['home_memory'])
    (memory / 'chat-a.jsonl').write_text(json.dumps(message) + '\n')
    for filename in ('chat-a.meta.json', 'chat-b.json'):
        path = memory / filename
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = {
            'id':'saved-team', 'agents':[{'id':'member', **RECIPE}]}
        if filename == 'chat-b.json':
            value['messages'] = [copy.deepcopy(message)]
        else:
            # JSONL metadata's unrelated fields are not a second message store.
            value['messages'] = None
        path.write_text(json.dumps(value))
    return legacy, stores, paths, message


def prepare(spec, tmp_path):
    target = tmp_path / 'app'
    fence = fence_legacy(spec, operation='images', target=target, namespace='image-agent')
    try:
        backup = backup_legacy(spec, fence=fence, directory=tmp_path / 'backup')
    except BaseException:
        fence.close()
        raise
    return fence, backup, target


def restore(fence, backup):
    return import_backup(backup['directory'], digest=backup['sha256'], fence=fence)


@pytest.mark.asyncio
async def test_both_formats_relocate_captured_stores_and_keep_other_data(images, tmp_path):
    spec, stores, paths, original = images
    before = {str(root): user_tree(root) for root in (Path(spec['project_config']), Path(spec['global_config']))}
    report = inspect_legacy(**spec)
    assert len([item for item in report['files'] if item['category'] == 'image-store']) == 3
    assert not any(issue['code'] == 'unclassified_source' for issue in report['issues'])
    fence, backup, target = prepare(spec, tmp_path)
    try:
        receipt = restore(fence, backup)
        assert restore(fence, backup) == receipt
        metadata = next((target / 'conversations').rglob('chat-a.meta.json'))
        assert json.loads(metadata.read_text())['messages'] is None
        for name in ('chat-a.jsonl', 'chat-b.json'):
            path = next((target / 'conversations').rglob(name))
            value = json.loads(path.read_text())
            message = value if name.endswith('.jsonl') else value['messages'][0]
            expected = ['file://' + str(target / image_destination(store.resolve()) / 'same-chat/same.png') for store in stores]
            for field in ('content', '_llm_content'):
                assert [b['image_url']['url'] for b in message[field][:3]] == expected
                assert message[field][3:] == original[field][3:]
            assert message['tool_calls'] == original['tool_calls']
            for old, ref in zip(paths, expected):
                copied = Path(ref.removeprefix('file://'))
                assert copied.read_bytes() == old.read_bytes()
                assert not copied.stat().st_mode & 0o077
            # A reopened App reads the captured copies, not Files or source stores.
            files = SimpleNamespace(call_tool=AsyncMock(side_effect=AssertionError('not a workspace image')))
            resolver = BoundImageResolver(image_root=target / 'configuration/.pantheon/images', files=files)
            assert [pixels(await resolver(ref))[1] for ref in expected] == [
                (255, 0, 0, 255), (0, 0, 255, 255), (0, 128, 0, 255)]
            files.call_tool.assert_not_called()
        assert {str(root): user_tree(root) for root in (Path(spec['project_config']), Path(spec['global_config']))} == before
    finally:
        fence.close()


@pytest.mark.parametrize('bad', ['missing', 'escape', 'symlink'])
def test_missing_and_escaping_image_sources_cannot_be_admitted(images, tmp_path, bad):
    spec, stores, paths, message = images
    if bad == 'symlink':
        paths[0].unlink()
        paths[0].symlink_to(paths[1])
        assert any(i['code'] == 'non_regular_file' for i in inspect_legacy(**spec)['issues'])
        with pytest.raises(ValueError, match='regular source trees'):
            prepare(spec, tmp_path)
        return
    reference = stores[0] / ('missing.png' if bad == 'missing' else '../outside.png')
    message['content'][0]['image_url']['url'] = 'file://' + str(reference)
    (Path(spec['home_memory']) / 'chat-a.jsonl').write_text(json.dumps(message)+'\n')
    fence, backup, target = prepare(spec, tmp_path)
    try:
        with pytest.raises(ValueError, match='Legacy image reference'):
            restore(fence, backup)
        assert not target.exists()
    finally:
        fence.close()


def test_interrupted_image_import_resumes_without_overwriting(images, tmp_path, monkeypatch):
    from pantheon.chatroom import migration_import
    spec, *_ = images
    fence, backup, target = prepare(spec, tmp_path)
    copy_file = migration_import._copy
    stopped = False
    def interrupt(snapshot, root, item):
        nonlocal stopped
        copy_file(snapshot, root, item)
        if item['category'] == 'image-store' and not stopped:
            stopped = True
            raise RuntimeError('interrupted after image copy')
    try:
        monkeypatch.setattr(migration_import, '_copy', interrupt)
        with pytest.raises(RuntimeError, match='interrupted'):
            restore(fence, backup)
        assert json.loads((target / 'migration.json').read_text())['phase'] == 'importing'
        monkeypatch.setattr(migration_import, '_copy', copy_file)
        receipt = restore(fence, backup)
        assert receipt['conversations'] == 2
        assert json.loads((target / 'migration.json').read_text())['phase'] == 'committed'
    finally:
        fence.close()


def test_large_jsonl_is_rewritten_one_message_at_a_time(images, tmp_path):
    spec, _, paths, _ = images
    line = json.dumps({'role':'user', 'content':[
        {'type':'text', 'text':'x'*1024},
        {'type':'image_url', 'image_url':{'url':'file://'+str(paths[0])}}]})+'\n'
    source = Path(spec['home_memory']) / 'chat-a.jsonl'
    with source.open('w') as stream:
        for _ in range(16000):
            stream.write(line)
    assert source.stat().st_size > 16*1024*1024
    fence, backup, target = prepare(spec, tmp_path)
    try:
        restore(fence, backup)
        restored = next((target / 'conversations').rglob('chat-a.jsonl'))
        with restored.open() as stream:
            for count, row in enumerate(stream, 1):
                message = json.loads(row)
                assert message['content'][0]['text'] == 'x'*1024
                assert message['content'][1]['image_url']['url'].startswith('file://'+str(target))
        assert count == 16000
    finally:
        fence.close()


def test_large_individual_message_preserves_payload_and_relocates_image(images, tmp_path):
    """Real histories contain single messages larger than the old 16 MiB cap."""
    from hashlib import sha256
    spec, stores, paths, _ = images
    payload = 'data:image/png;base64,' + 'A' * (17 * 1024 * 1024)
    original = {'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': payload}},
        {'type': 'image_url', 'image_url': {'url': 'file://' + str(paths[0])}},
        {'type': 'text', 'text': str(paths[0])}]}
    source = Path(spec['home_memory']) / 'chat-a.jsonl'
    # Also check byte preservation on a large message requiring no relocation.
    unchanged = json.dumps({'role': 'assistant', 'content': payload}, indent=None).encode() + b'\n'
    raw = json.dumps(original).encode() + b'\n'
    source.write_bytes(raw + unchanged)
    source_hash = sha256(source.read_bytes()).hexdigest()
    assert not any(i['code'] == 'invalid_conversation' for i in inspect_legacy(**spec)['issues'])
    fence, backup, target = prepare(spec, tmp_path)
    try:
        receipt = restore(fence, backup)
        assert restore(fence, backup) == receipt
        restored = next((target / 'conversations').rglob('chat-a.jsonl'))
        with restored.open('rb') as stream:
            actual = json.loads(stream.readline())
            assert stream.readline() == unchanged
            assert not stream.read(1)
        expected = copy.deepcopy(original)
        expected['content'][1]['image_url']['url'] = 'file://' + str(
            target / image_destination(stores[0].resolve()) / 'same-chat/same.png')
        assert actual == expected
        assert sha256(source.read_bytes()).hexdigest() == source_hash
    finally:
        fence.close()
