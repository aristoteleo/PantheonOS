import asyncio
import json
import subprocess
import sys

import httpx
import pytest

from pantheon.platform import owner_credentials as provisioning
from test_model_remote_credentials import Node, OWNER

KEY = 'pbk_' + 'a' * 43


@pytest.fixture
def node(monkeypatch):
    node = Node()
    node.closed = False
    async def close():
        node.closed = True
    node.close = close
    def connect(**kwargs):
        assert kwargs['owner'] == OWNER
        assert kwargs['credential'].endpoint == 'https://controller.test/control'
        assert kwargs['credential'].key == KEY
        return node
    monkeypatch.setattr(provisioning, 'OwnerCredentialLifecycle', connect)
    return node


def identity():
    return dict(protocol=1, fleet_id=OWNER, controller_url='https://controller.test/control')


async def provision(handler=None, **kwargs):
    def hub(request):
        assert str(request.url) == 'https://hub.test/api/fleet/apps/workload-identity'
        assert request.headers['Authorization'] == 'Bearer ' + KEY
        return handler(request) if handler else httpx.Response(200, json=identity())
    args = dict(hub='https://hub.test', key=KEY, owner=OWNER, node_ids=['test-node'], ref_prefix='owner-v1',
                transport=httpx.MockTransport(hub))
    args.update(kwargs)
    return await provisioning.provision_owner_credentials(**args)


@pytest.mark.asyncio
async def test_only_encrypted_vault_receives_key_and_output_has_exact_endpoints(node):
    result = await provision()
    assert node.received == [KEY, KEY] and node.closed
    assert result == dict(protocol=1, owner=OWNER, source='platform-key', nodes={'test-node': {
        'hub': dict(ref='node-secret://owner-v1-hub', endpoint='https://hub.test'),
        'controller': dict(ref='node-secret://owner-v1-controller', endpoint='https://controller.test/control')}})
    assert KEY not in json.dumps(result) + json.dumps(node.calls)
    # Vault lookup normalizes only an empty path. The descriptor retains the
    # original Hub base so /api routes do not accidentally gain a /v1 prefix.
    assert node.calls[0][1]['credential_endpoint'] == 'https://hub.test/v1'
    assert node.calls[2][1]['credential_endpoint'] == 'https://controller.test/control'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['owner', 'protocol', 'duplicate', 'redirect', 'revoked', 'oversize',
    'controller-http', 'controller-userinfo', 'controller-query', 'transport'])
async def test_untrusted_identity_does_not_deliver_credentials(node, failure):
    def handler(request):
        value = identity()
        if failure == 'owner': value['fleet_id'] = 'f_' + '0' * 16
        if failure == 'protocol': value['protocol'] = True
        if failure == 'duplicate': return httpx.Response(200, text='{"protocol":1,"protocol":1}')
        if failure == 'redirect': return httpx.Response(302, headers={'Location': 'https://other.test'})
        if failure == 'revoked': return httpx.Response(401, text=KEY)
        if failure == 'oversize': return httpx.Response(200, content=b'x' * 8193)
        if failure == 'controller-http': value['controller_url'] = 'http://controller.test'
        if failure == 'controller-userinfo': value['controller_url'] = 'https://user:secret@controller.test'
        if failure == 'controller-query': value['controller_url'] = 'https://controller.test?'
        if failure == 'transport': raise httpx.ConnectError(KEY, request=request)
        return httpx.Response(200, json=value)
    with pytest.raises(ValueError) as error:
        await provision(handler)
    assert KEY not in str(error.value) and not node.calls


@pytest.mark.asyncio
async def test_all_destinations_checked_before_any_mutation(node):
    checked = []
    async def status(name):
        checked.append(name)
        return node.state if name == 'test-node' else dict(node.state, node_id=name, owner='wrong')
    node.status = status
    with pytest.raises(ValueError, match='changed identity'):
        await provision(node_ids=['test-node', 'second-node'])
    assert checked == ['test-node', 'second-node'] and not node.calls and node.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [dict(key='short-lived-jwt'), dict(node_ids=[]),
    dict(node_ids=['test-node', 'test-node']), dict(ref_prefix='x'*54), dict(hub='http://hub.test')])
async def test_invalid_intent_rejected_before_network(node, changed):
    def forbidden(_):
        raise AssertionError('No request expected')
    with pytest.raises(ValueError):
        await provision(forbidden, **changed)
    assert not node.calls


@pytest.mark.asyncio
async def test_cancelled_accepted_delivery_drains_before_connection_closes(node):
    node.block = True
    task = asyncio.create_task(provision())
    await asyncio.wait_for(node.entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(.02)
    assert not node.closed and not task.done()
    node.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert node.closed and node.received == [KEY]


@pytest.mark.parametrize('unsafe', ['public', 'symlink', 'existing-output'])
def test_cli_rejects_unsafe_files_before_contacting_hub(tmp_path, monkeypatch, unsafe, capsys):
    key = tmp_path / 'key'
    key.write_text(KEY)
    key.chmod(0o600)
    output = tmp_path / 'output.json'
    if unsafe == 'public': key.chmod(0o644)
    elif unsafe == 'symlink':
        link = tmp_path / 'link'; link.symlink_to(key); key = link
    else: output.write_text('preserve')
    monkeypatch.setattr(sys, 'argv', ['provision', '--hub', 'https://hub.test', '--owner', OWNER,
        '--node-id', 'test-node', '--ref-prefix', 'owner-v1', '--key-file', str(key), '--output', str(output)])
    async def forbidden(**kwargs):
        raise AssertionError('No request expected')
    monkeypatch.setattr(provisioning, 'provision_owner_credentials', forbidden)
    with pytest.raises(SystemExit) as error:
        provisioning.main()
    assert error.value.code == 1 and KEY not in capsys.readouterr().err
    if unsafe == 'existing-output': assert output.read_text() == 'preserve'


def test_provisioner_has_no_agent_dependency():
    result = subprocess.run([sys.executable, '-c',
        'import sys; import pantheon.platform.owner_credentials; '
        'assert not any(k == "pantheon.agent" or k.startswith("pantheon.chatroom") for k in sys.modules)'],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
