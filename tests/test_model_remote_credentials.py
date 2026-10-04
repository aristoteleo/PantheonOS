"""Owner-side delivery validation; the native Fleet gate checks real Go storage."""
import asyncio
import base64
import json
import time
import secrets

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.credentials import RemoteModelCredentialVault
from pantheon.models.platform_budget import provision_platform_budget

OWNER = 'f_0123456789abcdef'
KEY = 'fixture-per-user-budget-key'


class Node(FleetLifecycle):
    def __init__(self):
        super().__init__(None)
        self.state = dict(owner=OWNER, node_id='test-node', credential_import_protocol=1)
        self.calls = []
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.challenge_mutation = lambda q: None
        self.received = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = False
        self.fail = False
        self.reply = {'ok': True}

    async def status(self, node):
        assert node == 'test-node'
        return self.state

    async def _request(self, node, method, **data):
        assert node == 'test-node'
        self.calls.append((method, data))
        if method == 'credential_prepare':
            q = dict(protocol=1, owner=OWNER, node_id=node, challenge_id=secrets.token_hex(16),
                     ref=data['credential_ref'], endpoint=data['credential_endpoint'], expires=int(time.time())+120,
                     public_key=base64.b64encode(self.private.public_key().public_bytes(
                         serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode(), context='')
            self.aad = json.dumps(q).encode()
            q['context'] = base64.b64encode(self.aad).decode()
            self.challenge_mutation(q)
            return q
        assert method == 'credential_ensure'
        envelope = data['credential_envelope']
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(envelope['public_key']))
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                       info=b'pantheon/node-credential-import/v1').derive(self.private.exchange(ec.ECDH(), peer))
        self.received.append(AESGCM(derived).decrypt(base64.b64decode(envelope['nonce']),
                            base64.b64decode(envelope['data']), self.aad).decode())
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError('secret error: ' + KEY)
        return self.reply


def vault(node):
    return RemoteModelCredentialVault(node, owner=OWNER, node_id='test-node')


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint,expected', [('https://api.test', 'https://api.test/v1'),
    ('https://api.test/prefix/v1/', 'https://api.test/prefix/v1'), ('http://127.0.0.1:8989/api','http://127.0.0.1:8989/api')])
async def test_encrypted_remote_delivery(endpoint, expected):
    node = Node()
    await vault(node).ensure_async('node-secret://budget', endpoint, KEY)
    assert node.received == [KEY]
    assert node.calls[0][1]['credential_endpoint'] == expected
    assert KEY not in json.dumps(node.calls)
    assert set(node.calls[1][1]) == {'credential_challenge', 'credential_envelope'}


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('owner','another'),('node_id','another'),('credential_import_protocol',0),
                                         ('credential_import_protocol',True)])
async def test_wrong_destination_before_sending(field,value):
    node = Node(); node.state[field] = value
    with pytest.raises(ValueError, match='identity'):
        await vault(node).ensure_async('node-secret://budget','https://api.test',KEY)
    assert not node.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('owner','another'),('node_id','another'),('protocol',True),('protocol',2),
    ('ref','node-secret://other'),('endpoint','https://other.test/v1'),('challenge_id','bad'),('expires',0),
    ('expires',2**40),('expires',True),('public_key','invalid'),('context','invalid'),('context','x'*8193)])
async def test_challenge_changes_rejected(field,value):
    node=Node(); node.challenge_mutation=lambda q:q.update({field:value})
    with pytest.raises(ValueError,match='delivery failed'):
        await vault(node).ensure_async('node-secret://budget','https://api.test',KEY)
    assert not node.received and len(node.calls)==1


@pytest.mark.asyncio
async def test_context_binds_all_fields():
    node=Node()
    def mutate(q):
        transcript=json.loads(base64.b64decode(q['context']));transcript['node_id']='other'
        q['context']=base64.b64encode(json.dumps(transcript).encode()).decode()
    node.challenge_mutation=mutate
    with pytest.raises(ValueError):
        await vault(node).ensure_async('node-secret://budget','https://api.test',KEY)
    assert not node.received


@pytest.mark.asyncio
@pytest.mark.parametrize('ref,endpoint,key',[('bad','https://api.test',KEY),('node-secret://ok','http://remote.test',KEY),
    ('node-secret://ok','https://user:pass@api.test',KEY),('node-secret://ok','https://api.test',''),
    ('node-secret://ok','https://api.test','x'*8193)])
async def test_invalid_input_sends_nothing(ref,endpoint,key):
    node=Node()
    with pytest.raises(ValueError):await vault(node).ensure_async(ref,endpoint,key)
    assert not node.calls


@pytest.mark.asyncio
async def test_cancel_joins_accepted_delivery():
    node=Node();node.block=True
    task=asyncio.create_task(vault(node).ensure_async('node-secret://budget','https://api.test',KEY))
    await asyncio.wait_for(node.entered.wait(),3)
    task.cancel();await asyncio.sleep(.02)
    assert not task.done()
    node.release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert node.received==[KEY]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['error','reply'])
async def test_remote_failure_sanitized_no_automatic_retry(failure):
    node=Node();node.fail=failure=='error';node.reply={'ok':False,'secret':KEY}
    with pytest.raises(ValueError) as exc:
        await vault(node).ensure_async('node-secret://budget','https://api.test',KEY)
    assert KEY not in str(exc.value)
    assert len(node.calls)==2


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['direct','openrouter'])
async def test_budget_acquisition_uses_remote_vault_without_exporting_login(mode):
    node=Node()
    def hub(request):
        assert request.headers['Authorization']=='Bearer full-owner-login'
        return httpx.Response(200,json=dict(fleet_id=OWNER,api_base_url='https://proxy.test/tenant/v1',
                                            model_mode=mode,virtual_key=KEY))
    descriptor=await provision_platform_budget(hub='https://hub.test',token='full-owner-login',vault=vault(node),
        ref='node-secret://budget',transport=httpx.MockTransport(hub))
    assert descriptor['connector']==dict(engine='api',endpoint='https://proxy.test/tenant/v1',secret_ref='node-secret://budget')
    assert node.received==[KEY]
    assert KEY not in json.dumps(node.calls) + json.dumps(descriptor)
    assert 'full-owner-login' not in json.dumps(node.calls) + json.dumps(descriptor)


@pytest.mark.asyncio
async def test_owner_delivery_transport_does_not_expand_allocator_authority():
    from pantheon.apps.runtime_config import RuntimeCredential
    from pantheon.apps.dependency_assembly import AssemblyError
    from pantheon.platform.dependency_control import OwnerCredentialLifecycle, OwnerDependencyLifecycle
    credential=RuntimeCredential('https://controller.test','owner-credential')
    for kind,method in [(OwnerCredentialLifecycle,'invoke'),(OwnerCredentialLifecycle,'submit'),
                        (OwnerDependencyLifecycle,'credential_prepare'),(OwnerDependencyLifecycle,'credential_ensure')]:
        client=kind(owner=OWNER,credential=credential)
        async def forbidden():raise AssertionError('Must reject before owner join')
        client.connect=forbidden
        try:
            with pytest.raises(AssemblyError):await client._request('test-node',method)
        finally:await client.close()


@pytest.mark.parametrize('failure',[None,'delivery','unsafe-controller-token','mixed-mode'])
def test_remote_budget_command_uses_private_credentials_and_closes_transport(tmp_path,monkeypatch,capsys,failure):
    import sys
    from pantheon.models import platform_budget
    from pantheon.platform import dependency_control
    token=tmp_path/'owner-login';token.write_text('full-owner-login');token.chmod(0o600)
    controller=tmp_path/'controller-login';controller.write_text('fleet-owner-key');controller.chmod(0o600)
    output=tmp_path/'descriptor.json'
    if failure=='unsafe-controller-token':controller.chmod(0o644)
    created=[]
    class Owner(Node):
        def __init__(self,*,owner,credential):
            super().__init__()
            assert owner==OWNER and credential.endpoint=='https://controller.test' and credential.key=='fleet-owner-key'
            self.closed=False;self.fail=failure=='delivery';created.append(self)
        async def close(self):self.closed=True
    monkeypatch.setattr(dependency_control,'OwnerCredentialLifecycle',Owner)
    original=platform_budget.provision_platform_budget
    async def provision(**kwargs):
        return await original(**kwargs,transport=httpx.MockTransport(lambda _: httpx.Response(200,json=dict(
            fleet_id=OWNER,api_base_url='https://proxy.test/v1',model_mode='direct',virtual_key=KEY))))
    monkeypatch.setattr(platform_budget,'provision_platform_budget',provision)
    args=['budget','--hub','https://hub.test','--token-file',str(token),'--controller','https://controller.test',
          '--controller-token-file',str(controller),'--owner',OWNER,'--node-id','test-node',
          '--ref','node-secret://budget','--output',str(output)]
    if failure=='mixed-mode':args+=['--fleet-executable','/unused/fleet','--state-dir','/unused/state']
    monkeypatch.setattr(sys,'argv',args)
    if failure:
        with pytest.raises(SystemExit) as exc:platform_budget.main()
        assert exc.value.code==1 and not output.exists()
    else:
        platform_budget.main()
        result=json.loads(output.read_text())
        assert result['connector']['secret_ref']=='node-secret://budget'
        assert output.stat().st_mode&0o777==0o600
        assert KEY not in output.read_text() and 'full-owner-login' not in output.read_text()
    assert all(n.closed for n in created)
    if failure in ('unsafe-controller-token','mixed-mode'):assert not created
    else:assert len(created)==1
    logs=capsys.readouterr()
    assert KEY not in logs.out+logs.err and 'full-owner-login' not in logs.out+logs.err and 'fleet-owner-key' not in logs.out+logs.err
