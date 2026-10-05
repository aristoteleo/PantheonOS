import asyncio
import json
from types import SimpleNamespace

import pytest

from pantheon.apps.modal_credentials import ModalCredentialOwner
from pantheon.apps.runtime_config import RuntimeCredential
from test_modal_app_owner import Modal, aio, owner


def credential(**changes):
    values = {'endpoint': 'https://api.modal.com', 'key': json.dumps({'token_id':'test-id', 'token_secret':'test-secret'})}
    return RuntimeCredential(**(values | changes))


@pytest.mark.asyncio
async def test_cancelled_client_open_is_joined_before_close(monkeypatch):
    import modal
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    class Client:
        def __init__(self, endpoint, kind, credentials):
            assert endpoint == 'https://api.modal.com'
            assert credentials == ('test-id', 'test-secret')
            calls.append('constructed')
        async def __aenter__(self):
            entered.set()
            await release.wait()
            calls.append('open')
            return self
        async def __aexit__(self, *args):
            calls.append('closed')
    monkeypatch.setattr(modal, 'Client', Client)
    monkeypatch.setenv('MODAL_SERVER_URL', 'https://unrelated.invalid')
    held = ModalCredentialOwner(credential())
    pending = asyncio.create_task(held.start())
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError): await pending
    closing = asyncio.create_task(held.close())
    await asyncio.sleep(.01)
    assert not closing.done() and calls == ['constructed']
    release.set()
    await closing
    await held.close()
    assert calls == ['constructed', 'open', 'closed'] and held._credentials == ()
    with pytest.raises(RuntimeError): await held.start()


@pytest.mark.parametrize('changes', [{'endpoint':'https://other.invalid'}, {'key':'not-json'},
    {'key':json.dumps({'token_id':'id', 'token_secret':'secret', 'extra':'value'})},
    {'key':json.dumps({'token_id':'id', 'token_secret':'bad\nvalue'})}])
def test_invalid_credentials_rejected(changes):
    with pytest.raises(ValueError, match='Invalid prepared'):
        ModalCredentialOwner(credential(**changes))


@pytest.mark.asyncio
async def test_same_explicit_client_handles_creation_and_lost_reply_recovery(tmp_path):
    sdk, supplied, seen = Modal(), object(), []
    original_app, original_create, original_name = sdk.app, sdk.create, sdk.by_name
    async def app(*args, client, **kwargs):
        assert client is supplied
        seen.append('app')
        return await original_app(*args, **kwargs)
    def image(identity, *, client):
        assert client is supplied
        seen.append('image')
        return identity
    async def create(*args, client, **kwargs):
        assert client is supplied
        seen.append('create')
        return await original_create(*args, **kwargs)
    async def by_name(*args, client):
        assert client is supplied
        seen.append('recover')
        return await original_name(*args)
    sdk.App.lookup, sdk.Image.from_id = aio(app), image
    sdk.Sandbox.create, sdk.Sandbox.from_name = aio(create), aio(by_name)
    sdk.lose_create = True
    held = owner(tmp_path, sdk, modal_client=supplied)
    with pytest.raises(OSError): await held.start()
    assert (await held.stop())['stopped']
    assert seen == ['app', 'image', 'create', 'recover']
    assert all('client' not in args['env'] and not args['secrets'] for _, args in sdk.calls)
