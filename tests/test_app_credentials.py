"""Ordinary App bus credentials reuse the authenticated model vault transport."""
import json

import pytest

from pantheon.apps.credentials import RemoteAppCredentialVault, app_credential_endpoint
from pantheon.models.credentials import RemoteModelCredentialVault
from test_model_remote_credentials import Node, OWNER, KEY


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint,expected', [
    ('nats://127.0.0.1:4222/', 'nats://127.0.0.1:4222'),
    ('nats://127.0.0.2:4222', 'nats://127.0.0.2:4222'),
    ('tls://bus.example:4222', 'tls://bus.example:4222'),
    ('ws://[::1]:8080/events/', 'ws://[::1]:8080/events/'),
    ('wss://bus.example/nats/', 'wss://bus.example/nats/'),
    ('https://store.example', 'https://store.example/v1'),
    ('https://store.example/api', 'https://store.example/api'),
])
async def test_bus_delivery_uses_encrypted_exact_endpoint_identity(endpoint, expected):
    node = Node()
    await RemoteAppCredentialVault(node, owner=OWNER, node_id='test-node').ensure_async(
        'node-secret://desktop', endpoint, KEY)
    assert node.received == [KEY]
    assert node.calls[0][1]['credential_endpoint'] == expected
    assert KEY not in json.dumps(node.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', [
    'nats://remote.example:4222', 'ws://remote.example/events',
    'tls://user:secret@bus.example', 'tls://@bus.example',
    'tls://bus.example:0', 'wss://bus.example:65536',
    'tls://bus.example/path', 'wss://bus.example?', 'wss://bus.example#',
    'file:///tmp/credentials', 'https://store.example\n',
])
async def test_invalid_app_endpoint_is_rejected_before_network(endpoint):
    node = Node()
    with pytest.raises(ValueError):
        await RemoteAppCredentialVault(node, owner=OWNER, node_id='test-node').ensure_async(
            'node-secret://desktop', endpoint, KEY)
    assert not node.calls and not node.received


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['nats://127.0.0.1:4222', 'wss://bus.example/nats/'])
async def test_model_configuration_remains_http_only(endpoint):
    node = Node()
    with pytest.raises(ValueError):
        await RemoteModelCredentialVault(node, owner=OWNER, node_id='test-node').ensure_async(
            'node-secret://provider', endpoint, KEY)
    assert not node.calls


def test_app_url_is_distinct_from_vault_http_identity():
    assert app_credential_endpoint('https://store.example/') == 'https://store.example'
    assert app_credential_endpoint('wss://bus.example/events/') == 'wss://bus.example/events/'
