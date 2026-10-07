import copy
import json

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.platform import preset_release

A1, A2, C1, D1 = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64


def app(revision, scope, generation, node='n_brain', extra=None):
    return {'node_id': node, 'revision': revision, 'scope': scope, 'generation': generation,
            'components': {'backend': {'values': extra or {}}}, 'bindings': {}}


def recipe(agent=A1):
    return {'owner': 'f_1', 'operation_id': 'agent-setup-1', 'kind': 'model-services',
            'apps': {'agent': app(agent, 'agent', 0), 'desktop': app(D1, 'shared-desktop', 0, 'n_ws')},
            'model_apps': {'connector': {'deployment_id': 'platform', 'name': 'P', 'models': [],
                                         'app': app(C1, 'model-platform', 0)}}}


def states(instances):
    """instances: [(node, digest, scope, generation, state)]."""
    out = {'n_brain': {'instances': {}}, 'n_ws': {'instances': {}}}
    for k, (node, digest, scope, generation, state) in enumerate(instances):
        out[node]['instances'][f'i{k}'] = {'digest': digest, 'scope': scope, 'generation': generation, 'state': state}
    return out


def test_only_changed_package_releases_are_an_update():
    assert preset_release.release_changes(recipe(), recipe(A2)) == {'agent': A2}
    with pytest.raises(AssemblyError, match='already running'):
        preset_release.release_changes(recipe(), recipe())
    changed = recipe(A2)
    changed['apps']['agent']['components']['backend']['values'] = {'new': 1}
    with pytest.raises(AssemblyError, match='configuration of agent'):
        preset_release.release_changes(recipe(), changed)
    moved = recipe(A2)
    moved['apps']['desktop']['node_id'] = 'n_brain'
    with pytest.raises(AssemblyError, match='configuration of desktop'):
        preset_release.release_changes(recipe(), moved)
    provider = recipe(A2)
    provider['model_apps']['connector']['app']['revision'] = 'e' * 64
    with pytest.raises(AssemblyError, match='Model provider'):
        preset_release.release_changes(recipe(), provider)
    extra = copy.deepcopy(recipe(A2))
    extra['apps']['shell'] = app('f' * 64, 'shared-shell', 0, 'n_ws')
    with pytest.raises(AssemblyError, match='topology'):
        preset_release.release_changes(recipe(), extra)


def test_upgrade_restarts_the_rest_and_rollback_returns_to_the_retained_generation():
    current = recipe()
    stopped = states([('n_brain', A1, 'agent', 7, 'stopped'), ('n_ws', D1, 'shared-desktop', 5, 'stopped'),
                      ('n_brain', C1, 'model-platform', 9, 'stopped')])
    generations = preset_release.current_generations(current, stopped)
    assert generations == {'agent': 7, 'desktop': 5, 'connector': 9}
    target = preset_release.with_generations(recipe(A2), {**generations, 'agent': 0}, 'agent-upgrade-1')
    assert target['apps']['agent']['generation'] == 0 and target['apps']['desktop']['generation'] == 5
    record = preset_release.receipt(current, target, {'agent': A2}, generations)
    assert record['origins'] == {'agent': {'digest': A1, 'scope': 'agent', 'generation': 7}}
    # The candidate ran and stopped; the source generation is still retained.
    after = states([('n_brain', A1, 'agent', 7, 'stopped'), ('n_brain', A2, 'agent', 3, 'stopped'),
                    ('n_ws', D1, 'shared-desktop', 8, 'stopped'), ('n_brain', C1, 'model-platform', 12, 'stopped')])
    assert preset_release.rollback_generations(record, after) == {'agent': 7, 'desktop': 8, 'connector': 12}
    # A source that moved on cannot be restored exactly.
    moved = states([('n_brain', A1, 'agent', 8, 'stopped'), ('n_ws', D1, 'shared-desktop', 8, 'stopped'),
                    ('n_brain', C1, 'model-platform', 12, 'stopped')])
    with pytest.raises(AssemblyError, match='retained agent'):
        preset_release.rollback_generations(record, moved)


def test_release_change_needs_every_app_stopped():
    with pytest.raises(AssemblyError, match='must be stopped'):
        preset_release.current_generations(recipe(), states([
            ('n_brain', A1, 'agent', 7, 'ready'), ('n_ws', D1, 'shared-desktop', 5, 'stopped'),
            ('n_brain', C1, 'model-platform', 9, 'stopped')]))


@pytest.mark.asyncio
async def test_owner_chosen_release_set_replaces_the_recommended_pin(tmp_path, monkeypatch):
    from pantheon.platform.service import PlatformService
    monkeypatch.setenv('PANTHEON_AGENT_RELEASE_URL', 'https://example.com/recommended.tar.gz')
    monkeypatch.setenv('PANTHEON_AGENT_RELEASE_SHA256', 'b' * 64)
    service = PlatformService()
    service._owner_state_directory = tmp_path
    service._write_private('agent-setup.json', {'release': {'url': 'https://example.com/run.tar.gz', 'sha256': 'a' * 64}})
    service._app_preset.recipe = {'apps': {}}
    chosen = []

    async def upgrade(setup, release):
        chosen.append(release)
        return {'changes': []}
    monkeypatch.setattr(service, '_release_upgrade', upgrade)
    store = {'url': 'https://example.com/store.tar.gz', 'sha256': 'c' * 64, 'ignored': 1}
    assert (await service.platform_agent_release('upgrade', target=store))['success']
    assert (await service.platform_agent_release('upgrade'))['success']
    assert chosen == [{'url': store['url'], 'sha256': store['sha256']},
                      {'url': 'https://example.com/recommended.tar.gz', 'sha256': 'b' * 64}]
    assert service._app_preset.held


def test_store_release_content_lists_each_app_identity(tmp_path):
    from pantheon.apps.publish_release_set import release_content
    (tmp_path / 'release-set.json').write_text(json.dumps({'protocol': 1, 'apps': {
        'agent': {'linux-amd64': {'app_id': 'agent', 'version': '0.7.2', 'revision': A1, 'bytes': 1, 'path': 'agent'}}}}))
    content = release_content(tmp_path, url='https://x/y.tar.gz', sha256=C1, version='0.7.3', platform='linux-amd64')
    assert content['apps'] == {'agent': {'app_id': 'agent', 'version': '0.7.2', 'revision': A1}}
    with pytest.raises(ValueError):
        release_content(tmp_path, url='https://x/y.tar.gz', sha256=C1, version='0.7.3', platform='darwin-arm64')
