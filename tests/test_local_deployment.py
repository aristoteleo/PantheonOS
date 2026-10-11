"""Single-machine profiles render to a controller deployment; their model
directory, configuration updates and release switches need no coordinator."""
import json
from pathlib import Path
import urllib.request
from urllib.error import HTTPError

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.local.deployment import DIRECTORY_SECRET, OWNER_SECRET, deployment_spec, release_archive, render
from pantheon.local import release as local_release
from pantheon.local import update as local_update
from pantheon.local.launch import LaunchJournal

CONTEXT = {'controller': 'https://127.0.0.1:9443', 'nats': 'nats://127.0.0.1:4222', 'trust_roots_pem': 'PEM',
           'workspace': '/work', 'directory_root': '/profile/models'}


def composed():
    """A composed profile in the shape compose_profile returns."""
    connector = {'engine': 'api', 'endpoint': 'https://models.example/v1', 'secret_ref': 'node-secret://selected-model'}
    return {'protocol': 1, 'packages': {}, 'apps': {
        'agent': {'package': 'agent', 'scope': 'agent', 'bindings': {
            'allocator': {'app_id': 'dependency-binding', 'component': 'backend', 'methods': {'bind': {}},
                          'provider': {'$app': 'allocator', 'component': 'backend', 'port': 'http'}}},
            'components': {'backend': {'values': {'agent': {
                'projects': [{'id': 'w', 'name': 'W', 'path': {'$local': 'workspace'}}],
                'rpc_origin': {'$local': 'controller'}, 'trust_roots_pem': {'$local': 'trust_roots_pem'},
                'models': {'deployments': {'local': {'$model': 'connector'}}}}},
                'credentials': {'budget': {'ref': 'node-secret://budget', 'endpoint': 'https://models.example/v1'}}}}},
        'allocator': {'package': 'allocator', 'scope': 'allocator', 'bindings': {}, 'components': {'backend': {
            'values': {'dependency_binding': {'policies': {'agent': {'consumer': {'$app': 'agent'}}}}},
            'credentials': {'hub': {'$local': 'owner_credential'}, 'controller': {'$local': 'owner_credential'}}}}},
        'desktop': {'package': 'desktop', 'scope': 'desktop', 'bindings': {}, 'components': {'backend': {
            'values': {'desktop': {'fleet': {'auth': 'creds-base64'}, 'event_prefix': {'$local': 'fleet_event_prefix'}}},
            'credentials': {'fleet': {'$local': 'fleet_credential'}}}}}},
        'model_apps': {'connector': {'deployment_id': 'local', 'name': 'Selected', 'models': [
            {'id': 'm1', 'context_limit': 8192}, {'id': 'pics', 'operations': ['image']}],
            'app': {'package': 'connector', 'scope': 'model-local', 'bindings': {},
                    'components': {'backend': {'values': {'connector': connector}}}}}}}


def test_render_uses_literal_single_machine_values_and_controller_secrets():
    apps, secrets = render(composed(), CONTEXT)
    agent = apps['agent']['config']['backend']['values']['agent']
    assert agent['projects'][0]['path'] == '/work' and agent['rpc_origin'] == CONTEXT['controller']
    assert agent['models']['deployments']['local'] == 'current'
    assert apps['agent']['config']['backend']['credentials']['budget'] == {'$secret': 'budget'}
    assert apps['agent']['bindings']['allocator']['$app'] == 'allocator'
    allocator = apps['allocator']['config']['backend']
    assert allocator['credentials'] == {'hub': {'$secret': OWNER_SECRET}, 'controller': {'$secret': OWNER_SECRET}}
    # The allocator names Agent sessions' providers late, like on a cloud Fleet.
    assert allocator['values']['dependency_binding']['policies']['agent']['consumer'] == {'$app': 'agent', 'late': True}
    desktop = apps['desktop']['config']['backend']['values']['desktop']
    assert desktop['fleet'] == {'auth': 'fleet-key', 'url': CONTEXT['nats'], 'controller_ca_pem': 'PEM'}
    assert desktop['event_prefix'] == {'$fleet': 'event_prefix'}
    connector = apps['connector']['config']['backend']
    assert connector['values']['connector']['secret_ref'] == {'$secret_ref': 'selected-model'}
    assert connector['credentials'] == {'directory': {'$secret': DIRECTORY_SECRET}}
    models = connector['values']['directory']['models']
    assert models == [{'id': 'm1', 'required': True, 'context_limit': 8192},
                      {'id': 'pics', 'required': True, 'operations': ['image']}]
    assert secrets == {OWNER_SECRET: CONTEXT['controller'], 'budget': 'https://models.example/v1',
                       'selected-model': None, DIRECTORY_SECRET: None}
    spec = deployment_spec(apps, 'file:///p/r.tar.gz', 'a' * 64, secrets)
    assert spec['secrets'] == sorted(secrets) and all(app['intent'] == 'running' for app in spec['apps'].values())


def test_unknown_local_values_are_refused():
    spec = composed()
    spec['apps']['agent']['components']['backend']['values']['agent']['x'] = {'$local': 'hub'}
    with pytest.raises(AssemblyError):
        render(spec, CONTEXT)


def test_release_archive_holds_the_index_and_artifacts_only(tmp_path):
    release = tmp_path / 'apps'
    (release / 'artifacts').mkdir(parents=True)
    (release / 'release-set.json').write_text('{"protocol": 2, "apps": {}}')
    (release / 'artifacts' / ('b' * 64)).write_bytes(b'payload')
    (release / 'agent').mkdir()
    (release / 'agent' / 'app.json').write_text('{}')
    releases = tmp_path / 'releases'
    releases.mkdir()
    url, sha = release_archive(release, releases)
    assert url.startswith('file://' + str(releases)) and len(sha) == 64
    import tarfile
    with tarfile.open(Path(url[len('file://'):])) as archive:
        assert sorted(archive.getnames()) == ['artifacts/' + 'b' * 64, 'release-set.json']
    assert release_archive(release, releases) == (url, sha)  # reused, same digest


def test_directory_server_serves_the_hub_api_over_the_profile_directory(tmp_path):
    import asyncio
    from pantheon.local.directory import DirectoryServer
    from pantheon.models.local_directory import LocalModelDirectory
    directory = LocalModelDirectory(tmp_path / 'models', owner='f_0123456789abcdef')
    asyncio.run(directory.initialize())
    with DirectoryServer(directory) as server:
        assert server.endpoint.startswith('http://127.0.0.1:') and server.endpoint.endswith('/api/model-services')

        def call(path, token=server.key):
            request = urllib.request.Request(server.endpoint + path, headers={'Authorization': 'Bearer ' + token})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        assert call('') == {'deployments': []}
        assert call('/routes') == {'routes': []}
        with pytest.raises(HTTPError) as denied:
            call('', token='wrong')
        assert denied.value.code == 401


def test_configuration_update_is_reviewed_by_digest_and_approved_without_starting_anything():
    source = composed()
    target = composed()
    target['apps']['agent']['components']['backend']['values']['agent']['projects'][0]['name'] = 'Renamed'
    review = local_update.review(source, target)
    assert [c['path'] for c in review['changes']] == ['/apps/agent/components/backend/values/agent/projects']
    assert local_update.approve(source, target, review['review_id']) == {
        'state': 'approved', 'review_id': review['review_id'], 'target_hash': review['target_hash']}
    with pytest.raises(AssemblyError):
        local_update.approve(source, composed(), review['review_id'])
    moved = composed()
    moved['apps']['agent']['scope'] = 'other'
    with pytest.raises(AssemblyError):
        local_update.review(source, moved)


def test_release_switch_saves_the_launch_and_can_be_rolled_back(tmp_path, monkeypatch):
    launch_path = tmp_path / 'launch.json'
    profile = tmp_path / 'profile'
    launch = {'protocol': 1, 'launcher': ['/usr/bin/python3', '-m', 'pantheon'], 'bundle': str(tmp_path / 'v1'),
              'setup': str(tmp_path / 'setup.json'), 'profile': str(profile), 'workspace': str(tmp_path)}
    LaunchJournal(tmp_path)._write(launch_path, launch)
    revisions = {str(tmp_path / 'v1'): 'a' * 64, str(tmp_path / 'v2'): 'b' * 64}
    monkeypatch.setattr(local_release, '_compose', lambda bundle, setup: {'packages': {'agent': revisions[bundle]}})
    review, _ = local_release.review(launch_path, target_bundle=str(tmp_path / 'v2'))
    assert (review['mode'], review['data_policy']) == ('upgrade', 'copy-source-data')
    result = local_release.approve(launch_path, review['review_id'], target_bundle=str(tmp_path / 'v2'))
    assert result['launch']['bundle'] == str(tmp_path / 'v2')
    assert json.loads(launch_path.read_text())['bundle'] == str(tmp_path / 'v2')
    history = local_release.state(launch_path)['history']
    assert history == [{'review_id': review['review_id'], 'source_bundle': str(tmp_path / 'v1'),
                        'target_bundle': str(tmp_path / 'v2')}]
    back, _ = local_release.review(launch_path, rollback_of=review['review_id'])
    assert (back['mode'], back['data_policy'], back['target_bundle']) == ('rollback', 'retained-source-data', str(tmp_path / 'v1'))
    local_release.approve(launch_path, back['review_id'], rollback_of=review['review_id'])
    assert json.loads(launch_path.read_text())['bundle'] == str(tmp_path / 'v1')
    assert local_release.state(launch_path)['history'] == []
