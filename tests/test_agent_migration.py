"""Migration inventory must describe real stores without mutating or leaking them."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pantheon.chatroom.migration import inspect_legacy


@pytest.fixture
def legacy(tmp_path):
    project = tmp_path / 'project'
    config = project / '.pantheon'
    memory = config / 'memory'
    memory.mkdir(parents=True)
    metadata = {'id': 'chat-a', 'name': 'Saved conversation', 'extra_data': {
        'team_template': {'id': 'team-existing', 'agents': [{'id': 'member-existing', 'name': 'Researcher'}]},
        'project': {'path': str(project), 'name': 'Research'},
        'session_storage': {'metadata': {'customTitle': 'Saved conversation'}}}}
    (memory / 'chat-a.meta.json').write_text(json.dumps(metadata))
    (memory / 'chat-a.jsonl').write_text(json.dumps({'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'asset://external-image'}}]}) + '\n')
    (memory / 'chat-b.json').write_text(json.dumps({'id': 'chat-b', 'name': 'Older format',
        'messages': [{'role': 'assistant', 'content': 'saved answer'}], 'extra_data': {}}))
    for name in ('agents', 'teams', 'skills', 'memory-store', 'learning', 'brain'):
        (config / name).mkdir()
        (config / name / 'saved.md').write_text('Saved ' + name)
    (config / 'MEMORY.md').write_text('Persistent memory index')
    (config / 'settings.json').write_text('{"api_keys":{"OPENAI_API_KEY":"private-do-not-read"}}')
    (project / 'external-image.png').write_bytes(b'project asset stays here')
    user = tmp_path / 'user'
    user.mkdir()
    (user / 'projects.json').write_text('{}')
    spec = dict(projects=[{'id': 'stable-project', 'name': 'Research', 'path': str(project)}],
                active_project='stable-project', default_project='stable-project',
                home_memory=str(memory), global_config=str(user), project_config=str(config))
    return spec


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def test_read_only_report_preserves_project_conversation_and_team_identity(legacy, tmp_path, monkeypatch):
    before = tree(tmp_path)
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.name == 'settings.json': pytest.fail('Inventory read a credential-bearing settings file')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    report = inspect_legacy(**legacy)
    assert report == inspect_legacy(**legacy)
    assert report['projects'] == legacy['projects']
    assert report['requires_writer_fence'] and not report['ready_to_import']
    assert report['conversations'] == [
        {'id': 'chat-a', 'project_id': 'stable-project', 'format': 'jsonl', 'messages': 1, 'embedded_team': True},
        {'id': 'chat-b', 'project_id': 'stable-project', 'format': 'json', 'messages': 1, 'embedded_team': False}]
    prefix = 'conversations/projects/' + sha256(b'stable-project').hexdigest()
    assert len(report['stores']) == 1 and report['stores'][0]['target'] == prefix
    assert len(report['files']) == 10
    for item in report['files']:
        assert sha256(Path(item['source']).read_bytes()).hexdigest() == item['sha256']
        assert item['target'].startswith((prefix, 'configuration/.pantheon/'))
    assert [item['code'] for item in report['issues']] == ['configuration_requires_explicit_conversion']
    serialized = json.dumps(report)
    assert 'private-do-not-read' not in serialized and 'saved answer' not in serialized
    monkeypatch.setattr(Path, 'open', original)
    assert tree(tmp_path) == before
    (Path(legacy['home_memory']) / 'chat-a.jsonl').write_text('{"role":"assistant","content":"changed"}\n')
    assert inspect_legacy(**legacy)['sha256'] != report['sha256']


@pytest.mark.parametrize('kind', ['duplicate', 'malformed', 'wrong-id', 'missing-meta', 'ambiguous', 'symlink', 'unknown'])
def test_inventory_exposes_conflicts_instead_of_silently_dropping_history(legacy, tmp_path, kind):
    memory = Path(legacy['home_memory'])
    expected = 'invalid_conversation'
    if kind == 'duplicate':
        second = tmp_path / 'second'
        second.mkdir()
        (second / 'chat-b.json').write_bytes((memory / 'chat-b.json').read_bytes())
        legacy['home_memory'] = str(second)
        expected = 'duplicate_conversation_id'
    elif kind == 'malformed': (memory / 'chat-a.jsonl').write_text('{unfinished')
    elif kind == 'wrong-id':
        value = json.loads((memory / 'chat-b.json').read_text()); value['id'] = 'other'
        (memory / 'chat-b.json').write_text(json.dumps(value))
    elif kind == 'missing-meta': (memory / 'chat-a.meta.json').unlink()
    elif kind == 'ambiguous': (memory / 'chat-a.json').write_bytes((memory / 'chat-b.json').read_bytes())
    elif kind == 'symlink':
        (memory / 'linked.json').symlink_to(memory / 'chat-b.json')
        expected = 'non_regular_file'
    else:
        (memory / 'unexpected.bin').write_bytes(b'unknown history companion')
        expected = 'unrecognized_conversation_file'
    assert expected in {issue['code'] for issue in inspect_legacy(**legacy)['issues']}


def test_other_project_configurations_and_custom_memory_need_explicit_mapping(legacy, tmp_path):
    project = tmp_path / 'second'
    config = project / '.pantheon'
    (config / 'teams').mkdir(parents=True)
    (config / 'teams/custom.md').write_text('Other project template')
    custom = tmp_path / 'custom-memory'
    custom.mkdir()
    (custom / 'chat-custom.json').write_text(json.dumps({'id': 'chat-custom', 'name': 'Custom', 'messages': []}))
    legacy['projects'].append({'id': 'other', 'name': 'Other', 'path': str(project)})
    legacy['memory_overrides'] = {'other': str(custom)}
    report = inspect_legacy(**legacy)
    assert any(c['id'] == 'chat-custom' and c['project_id'] == 'other' for c in report['conversations'])
    assert any(i['code'] == 'additional_project_configuration_needs_scope_mapping' for i in report['issues'])
    assert next(f for f in report['files'] if f['source'].endswith('custom.md'))['target'] is None
    legacy['memory_overrides']['missing-project'] = str(custom)
    with pytest.raises(ValueError): inspect_legacy(**legacy)


def test_cli_writes_private_report_and_does_not_overwrite(legacy, tmp_path):
    spec, output = tmp_path / 'spec.json', tmp_path / 'report.json'
    spec.write_text(json.dumps(legacy))
    args = [sys.executable, '-m', 'pantheon.chatroom.migration', '--spec', str(spec), '--output', str(output)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert not output.stat().st_mode & 0o077
    before = output.read_bytes()
    assert json.loads(before) == inspect_legacy(**legacy)
    assert subprocess.run(args, capture_output=True).returncode != 0
    assert output.read_bytes() == before
