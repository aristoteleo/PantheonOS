import base64

import pytest

from pantheon.chatroom.app_models import AppSettings
from pantheon.chatroom.skill_files import AgentSkillFiles, CHUNK, PAGE, MAX_EDIT


@pytest.fixture
def files(tmp_path):
    return AgentSkillFiles(AppSettings(tmp_path / 'app', defaults={}, environment={}))


def test_create_read_edit_conflict_and_move_use_only_private_scopes(files):
    assert files.call('list', 'project')['files'] == []
    created = files.call('write', 'project', 'demo/SKILL.md', content='# Demo')
    assert created['success']
    listing = files.call('list', 'project')['files']
    assert [f['name'] for f in listing] == ['demo']
    read = files.call('read', 'project', 'demo/SKILL.md')
    assert base64.b64decode(read['data']) == b'# Demo'
    assert read['revision'] == created['revision']
    assert not files.call('write', 'project', 'demo/SKILL.md', content='accidental replacement')['success']
    updated = files.call('write', 'project', 'demo/SKILL.md', content='# Updated', revision=read['revision'])
    assert updated['success']
    assert files.call('write', 'project', 'demo/SKILL.md', content='stale', revision=read['revision'])['conflict']
    assert files.call('move', 'project', 'demo', target_scope='global')['success']
    assert files.call('list', 'project')['files'] == []
    assert base64.b64decode(files.call('read', 'global', 'demo/SKILL.md')['data']) == b'# Updated'
    files.call('delete', 'global', 'demo')
    assert files.call('list', 'global')['files'] == []


def test_large_resources_use_revision_checked_bounded_reads(files):
    path = files.roots['global'] / 'large.bin'
    path.parent.mkdir(parents=True)
    data = b'abc\x00' * CHUNK
    path.write_bytes(data)
    first = files.call('read', 'global', 'large.bin')
    assert len(base64.b64decode(first['data'])) == CHUNK and not first['eof']
    output = bytearray(base64.b64decode(first['data']))
    offset = first['next_offset']
    while offset < len(data):
        result = files.call('read', 'global', 'large.bin', offset=offset, revision=first['revision'])
        output.extend(base64.b64decode(result['data']))
        offset = result['next_offset']
    assert result['eof'] and output == data
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        files.call('read', 'global', 'large.bin', revision=first['revision'])


def test_large_directories_are_paged_without_losing_entries(files):
    root = files.roots['project']
    root.mkdir(parents=True)
    for index in range(PAGE+3):
        (root/f'{index:04}.md').write_text('')
    first = files.call('list', 'project')
    assert len(first['files']) == PAGE
    second = files.call('list', 'project', offset=first['next_offset'])
    assert len(second['files']) == 3 and second['next_offset'] is None
    assert len({row['name'] for row in first['files']+second['files']}) == PAGE+3


@pytest.mark.parametrize('scope,path', [('unknown', 'file'), ('project', '../settings.json'),
    ('project', '/etc/passwd'), ('project', 'a/../../file'), ('global', r'..\file'), ('project', './x')])
def test_rejects_non_skill_paths(files, scope, path):
    with pytest.raises(ValueError):
        files.call('write', scope, path, content='forbidden')


def test_scope_root_and_symlink_targets_cannot_be_edited(files, tmp_path):
    for operation in ('write', 'delete', 'move', 'mkdir'):
        with pytest.raises(ValueError):
            files.call(operation, 'project', '', content='forbidden')
    secret = tmp_path / 'secret'
    secret.write_text('private')
    root = files.roots['project']
    root.mkdir(parents=True)
    (root/'linked').symlink_to(secret)
    for operation in ('read', 'write', 'delete'):
        with pytest.raises(ValueError):
            files.call(operation, 'project', 'linked', content='forbidden')
    assert secret.read_text() == 'private'


def test_failed_save_keeps_previous_content_and_cleans_staging(files, monkeypatch):
    saved = files.call('write', 'project', 'demo/SKILL.md', content='old')
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr('pantheon.chatroom.skill_files.os.replace', fail)
    with pytest.raises(OSError):
        files.call('write', 'project', 'demo/SKILL.md', content='new', revision=saved['revision'])
    assert base64.b64decode(files.call('read', 'project', 'demo/SKILL.md')['data']) == b'old'
    assert not list(files.roots['project'].rglob('.skill-edit-*'))
    with pytest.raises(ValueError, match='64 KiB'):
        files.call('write', 'project', 'oversized', content='x'*(MAX_EDIT+1))
