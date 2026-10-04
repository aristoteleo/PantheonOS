import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from pantheon.chatroom.app_models import AppSettings
from pantheon.chatroom.settings_document import AgentSettingsDocument


def settings(root):
    return AppSettings(root, defaults={'models': {'provider_priority': ['openai']}}, environment={})


def test_saved_revision_is_private_pending_and_preserves_unmanaged_fields(tmp_path):
    live = settings(tmp_path)
    path = live.pantheon_dir / live.SETTINGS_FILE
    path.parent.mkdir(parents=True)
    path.write_text('// existing private configuration\n' + json.dumps({
        'api_keys': {'OPENAI_API_KEY': 'do-not-return'}, 'endpoint': {'max_file_read_lines': 20}}))
    document = AgentSettingsDocument(live)
    first = document.read()
    assert 'do-not-return' not in json.dumps(first)
    assert 'endpoint' not in first['overrides']
    saved = document.save(first['revision'], {'models': {'provider_priority': ['gemini']}})
    assert saved['success'] and saved['restart_required']
    assert live.get('models.provider_priority') == ['openai']
    assert saved['effective']['models']['provider_priority'] == ['openai']
    assert json.loads(path.read_text())['api_keys']['OPENAI_API_KEY'] == 'do-not-return'
    restarted = AgentSettingsDocument(settings(tmp_path)).read()
    assert not restarted['restart_required']
    assert restarted['effective']['models']['provider_priority'] == ['gemini']
    assert restarted['revision'] == saved['revision']
    assert not path.stat().st_mode & 0o077


def test_two_views_cannot_overwrite_each_others_revision(tmp_path):
    document = AgentSettingsDocument(settings(tmp_path))
    first = document.read()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda value: document.save(first['revision'],
            {'models': {'provider_priority': [value]}}), ['openai', 'gemini']))
    assert sum(result['success'] for result in results) == 1
    assert next(result for result in results if not result['success'])['conflict']
    assert document.read()['overrides'] == next(result['overrides'] for result in results if result['success'])


@pytest.mark.parametrize('overrides', [
    {'api_keys': {}}, {'env_file': '/outside'}, {'remote': {}}, {'services': {}},
    {'endpoint': {}}, {'models': []}, {'llm_retry': {'x': float('nan')}},
    {'models': {'name': 'a'*70_000}}, [],
])
def test_rejects_authority_changes_invalid_types_and_unbounded_documents(tmp_path, overrides):
    document = AgentSettingsDocument(settings(tmp_path))
    first = document.read()
    with pytest.raises(ValueError):
        document.save(first['revision'], overrides)
    assert document.read() == first


def test_failed_atomic_save_preserves_document_and_cleans_staging(tmp_path, monkeypatch):
    document = AgentSettingsDocument(settings(tmp_path))
    first = document.read()
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr('pantheon.chatroom.settings_document.os.replace', fail)
    with pytest.raises(OSError, match='disk failure'):
        document.save(first['revision'], {'models': {}})
    assert document.read() == first
    assert not list(tmp_path.rglob('.settings-*'))
