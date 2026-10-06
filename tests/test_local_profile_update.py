"""Reviewed configuration updates preserve the ordinary profile restart rules."""
from copy import deepcopy
import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import nats
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.models.bootstrap import digest
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from pantheon.platform.local_profile_update import review_update, approve_update, configuration_target, UpdateJournal
from test_local_fleet import binaries, assert_stopped
from test_local_profile import offline_session, minimal_manifest, settle


def candidate(spec):
    target = deepcopy(spec)
    target['apps']['consumer']['components'] = {'backend': {'values': {'setting': 'new'}}}
    return target


def test_update_command_imports_without_agent_execution():
    result = subprocess.run([sys.executable, '-c', '''import sys
import pantheon.platform.local_profile_update
assert not any(n == 'pantheon.agent' or n.startswith('pantheon.chatroom') for n in sys.modules)
'''], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


async def stopped_offline(tmp_path):
    runtime, spec, session = offline_session(tmp_path)
    await session._open()
    session._record['phase'] = 'stopped'
    await session._checkpoint(session.path, session._record)
    session._restart_state = AsyncMock(return_value=({}, {}))
    session.wire = AsyncMock()
    revision = spec['packages']['consumer']['revision']
    session.wire.status.return_value = {'owner': session.info.fleet_id, 'node_id': session.info.node_id,
        'instances': {}, 'dependency_config_protocol': 1, 'operations': {}, 'installations': {revision: {'state': 'installed'}}}
    session.wire.manifest.return_value = {'protocol': 1, 'revision': revision,
        'manifest': {'apiVersion': 2, 'id': 'model-consumer', 'version': '1.0.0'},
        'definition': {'components': [{'name': 'backend', 'configuration': {'values': {'setting': {'required': True}},
            'credentials': {'chosen': {'required': False}}}}]}}
    return runtime, spec, session


@pytest.mark.asyncio
async def test_review_is_read_only_and_approval_survives_without_rewriting_checkpoint(tmp_path):
    runtime, spec, session = await stopped_offline(tmp_path)
    target = candidate(spec)
    target['apps']['consumer']['components']['backend']['credentials'] = {
        'chosen': {'ref': 'node-secret://private-name', 'endpoint': 'https://private.example'}}
    before = session.path.read_bytes()
    review = review_update(session, target)
    assert review['changes'] and 'private-name' not in json.dumps(review)
    assert session.path.read_bytes() == before and not session.wire.mock_calls
    with pytest.raises(AssemblyError, match='profile changed'):
        LocalAppProfile(runtime, target, object())._load()
    result = await approve_update(session, target, review['review_id'])
    assert result['state'] == 'approved' and session.path.read_bytes() == before
    session.wire.submit.assert_not_called()
    session._restart_state.assert_awaited_once()
    receipt = session.root/'configuration-updates'/(review['review_id']+'.json')
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text())['source'] == spec
    fresh = LocalAppProfile(runtime, target, object())
    assert fresh._load()['manifest_hash'] == digest(spec)
    # Approval alone is reversible: no App/data/configuration has been changed.
    assert LocalAppProfile(runtime, spec, object())._load()['phase'] == 'stopped'
    with pytest.raises(AssemblyError):
        review_update(fresh, candidate(target))


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['candidate', 'checkpoint', 'token', 'live', 'resources', 'reservation', 'generation', 'render'])
async def test_changed_or_unstopped_inputs_never_publish_approval(tmp_path, damage):
    _, spec, session = await stopped_offline(tmp_path)
    target = candidate(spec)
    review = review_update(session, target)
    if damage == 'candidate': target['apps']['consumer']['components']['backend']['values']['setting'] = 'other'
    elif damage == 'checkpoint':
        session._record['cycle'] += 1
        session._record['recipe']['operation_id'] = 'local-profile-2'
        await session._checkpoint(session.path, session._record)
    elif damage == 'token': review['review_id'] = '0'*64
    elif damage in ('live', 'resources', 'reservation'):
        session.wire.status.return_value['instances']['app'] = {'state': 'running' if damage == 'live' else 'stopped',
            'resources': ['used'] if damage == 'resources' else [], 'reservations': ['used'] if damage == 'reservation' else []}
    elif damage == 'generation': session._restart_state.side_effect = AssemblyError('stale generation')
    else: target['apps']['consumer']['bindings'] = {'broken': 'invalid'}; review = review_update(session, target)
    before = session.path.read_bytes()
    with pytest.raises(AssemblyError):
        await approve_update(session, target, review['review_id'])
    assert not (session.root/'approved-update.json').exists()
    assert session.path.read_bytes() == before
    session.wire.submit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['receipt', 'pointer-link', 'receipt-link', 'stale', 'target'])
async def test_approval_cannot_be_reused_for_another_candidate_or_checkpoint(tmp_path, damage):
    runtime, spec, session = await stopped_offline(tmp_path)
    target = candidate(spec)
    review = review_update(session, target)
    await approve_update(session, target, review['review_id'])
    receipt = session.root/'configuration-updates'/(review['review_id']+'.json')
    if damage == 'receipt':
        data = json.loads(receipt.read_text()); data['target']['apps']['consumer']['scope'] = 'elsewhere'
        receipt.write_text(json.dumps(data))
    elif damage.endswith('-link'):
        path = receipt if damage == 'receipt-link' else session.root/'approved-update.json'
        saved = path.with_suffix('.saved'); path.rename(saved); path.symlink_to(saved)
    elif damage == 'stale':
        record = json.loads(session.path.read_text()); record['origin'] = 'https://127.0.0.1:19000'
        await session._checkpoint(session.path, record)
    else: target['apps']['consumer']['components']['backend']['values']['setting'] = 'unreviewed'
    fresh = LocalAppProfile(runtime, target, object())
    fresh.wire = AsyncMock()
    with pytest.raises(AssemblyError): await fresh.advance()
    assert not fresh.wire.mock_calls


@pytest.mark.parametrize('damage', ['package', 'scope', 'app-set', 'model-publication', 'unchanged'])
def test_configuration_update_does_not_claim_package_or_topology_upgrade(tmp_path, damage):
    spec = minimal_manifest(tmp_path)
    target = candidate(spec)
    if damage == 'package': target['packages']['consumer']['revision'] = '0'*64
    elif damage == 'scope': target['apps']['consumer']['scope'] = 'replacement'
    elif damage == 'app-set': target['apps']['extra'] = deepcopy(target['apps']['consumer'])
    elif damage == 'model-publication':
        target['model_apps']['model'] = {'app': deepcopy(target['apps']['consumer']), 'deployment_id': 'new', 'name': 'New', 'models': []}
    else: target = deepcopy(spec)
    with pytest.raises(AssemblyError): configuration_target(spec, target)


@pytest.mark.asyncio
@pytest.mark.parametrize('after_pointer', [False, True])
async def test_interrupted_approval_resumes_exact_receipt_without_touching_apps(tmp_path, monkeypatch, after_pointer):
    runtime, spec, session = await stopped_offline(tmp_path)
    target = candidate(spec)
    review = review_update(session, target)
    before = session.path.read_bytes()
    write = UpdateJournal._write
    def lose_ack(journal, path, value):
        write(journal, path, value)
        if path.name == ('approved-update.json' if after_pointer else review['review_id']+'.json'):
            raise OSError('injected loss after durable write')
    monkeypatch.setattr(UpdateJournal, '_write', lose_ack)
    with pytest.raises(OSError): await approve_update(session, target, review['review_id'])
    fresh = LocalAppProfile(runtime, target, object())
    if after_pointer:
        assert fresh._load()['phase'] == 'stopped'
    else:
        with pytest.raises(AssemblyError): fresh._load()
    monkeypatch.setattr(UpdateJournal, '_write', write)
    assert (await approve_update(session, target, review['review_id']))['state'] == 'approved'
    assert session.path.read_bytes() == before
    assert len(list((session.root/'configuration-updates').glob('*.json'))) == 1
    session.wire.submit.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_approvals_cannot_replace_the_in_progress_decision(tmp_path):
    _, spec, session = await stopped_offline(tmp_path)
    target = candidate(spec)
    review = review_update(session, target)
    entered, release = asyncio.Event(), asyncio.Event()
    async def hold(_):
        entered.set()
        await release.wait()
        return {}, {}
    session._restart_state.side_effect = hold
    first = asyncio.create_task(approve_update(session, target, review['review_id']))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(TimeoutError): await approve_update(session, target, review['review_id'])
        release.set()
        assert (await first)['state'] == 'approved'
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_configuration_update_and_reversal_keep_instance_data(tmp_path, binaries):
    spec = minimal_manifest(tmp_path)
    package = tmp_path/'app'
    script = package/'server.py'
    script.write_text('''import json,os,sys
from pathlib import Path
cfg=json.loads(Path(os.environ['PANTHEON_APP_CONFIG']).read_text())
with (Path(sys.argv[1])/'settings.jsonl').open('a') as output:
 output.write(json.dumps(cfg['values'])+'\\n')
''' + script.read_text())
    definition_path = package/'fleet.json'
    definition = json.loads(definition_path.read_text())
    definition['components'][0]['argv'].append('${DATA}')
    definition['components'][0]['configuration'] = {'values': {'setting': {'required': True}}}
    definition_path.write_text(json.dumps(definition))
    spec['packages']['consumer']['revision'] = build_artifact(package)[1]
    spec['apps']['consumer']['components'] = {'backend': {'values': {'setting': 'old'}}}
    target = candidate(spec)
    identity = None
    for cycle, current in enumerate((spec, target, spec), 1):
        async with LocalFleet(tmp_path/'profile', binaries, workspace=tmp_path) as runtime:
            info, children = runtime.coordinates, list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials), inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
            session = LocalAppProfile(runtime, current, resolver)
            try:
                assert (await settle(session, 'advance'))['state'] == 'ready'
                binding = session.app_binding('consumer')
                assert identity in (None, binding['instance_id'])
                identity = binding['instance_id']
                data = runtime.root/'node/apps'/info.fleet_id/'data'/identity/'settings.jsonl'
                assert [json.loads(line)['setting'] for line in data.read_text().splitlines()] == ['old', 'new', 'old'][:cycle]
                with pytest.raises(AssemblyError, match='Stop the original'):
                    review_update(session, target if cycle != 2 else spec)
                assert (await settle(session, 'stop'))['state'] == 'stopped'
                if cycle < 3:
                    next_spec = target if cycle == 1 else spec
                    review = review_update(session, next_spec)
                    before = session.path.read_bytes()
                    await approve_update(session, next_spec, review['review_id'])
                    assert session.path.read_bytes() == before
            finally:
                try:
                    state = await session.wire.status(info.node_id)
                    for item in state['instances'].values():
                        if item['state'] == 'stopped': continue
                        op = await session.wire.submit(info.node_id, 'stop', item['digest'],
                            generation=item['generation'], scope=item['scope'])
                        async with asyncio.timeout(60):
                            while (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]['state'] in ('queued', 'running'):
                                await asyncio.sleep(.05)
                finally:
                    await resolver.close()
        assert_stopped(children, info)
