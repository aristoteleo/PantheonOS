import base64
import json
import time
from types import SimpleNamespace

import httpx
import nkeys
import pytest

from pantheon.apps.owned_bus import BusConfiguration, FleetKeyCredential

KEY = 'pbk_' + 'a' * 43


def credential(endpoint='https://fleet.example', key=KEY):
    return SimpleNamespace(endpoint=endpoint, key=key)


def creds(exp):
    user = nkeys.from_seed(nkeys.encode_seed(b'\x01' * 32, nkeys.PREFIX_BYTE_USER))
    claims = base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=')
    jwt = 'eyJ0eXAiOiJKV1QifQ.' + claims + '.sig'
    seed = user.seed.decode()
    text = (f'-----BEGIN NATS USER JWT-----\n{jwt}\n------END NATS USER JWT------\n\n'
            f'-----BEGIN USER NKEY SEED-----\n{seed}\n------END USER NKEY SEED------\n')
    return text, user


@pytest.mark.parametrize('config,cred', [
    ({'auth': 'fleet-key', 'url': 'nats://fleet.example:4222'}, credential()),   # no TLS off loopback
    ({'auth': 'fleet-key', 'url': 'wss://fleet.example/nats', 'x': 1}, credential()),
    ({'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}, credential(endpoint='http://fleet.example')),
    ({'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}, credential(key='eyJ.session.jwt')),
    ({'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}, None),
])
def test_fleet_key_configuration_is_strict(config, cred):
    with pytest.raises(ValueError):
        BusConfiguration.parse(config, cred)


def test_fleet_key_configuration_binds_controller_and_bus():
    bus = BusConfiguration.parse({'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}, credential('https://fleet.example/'))
    assert (bus.endpoint, bus.auth, bus.controller) == ('wss://fleet.example/nats', 'fleet-key', 'https://fleet.example')
    assert KEY not in repr(bus)


@pytest.mark.asyncio
async def test_join_mints_signs_and_schedules_renewal_before_expiry():
    now = time.time()
    text, user = creds(now + 3600)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'fleet_id': 'f_0123456789abcdef', 'nats_url': 'nats://x', 'creds': text})
    cred = FleetKeyCredential('https://fleet.example', KEY, transport=httpx.MockTransport(handler))
    await cred.join()
    assert calls == [{'key': KEY}] and cred.fleet_id == 'f_0123456789abcdef'
    assert cred.user_jwt().decode().count('.') == 2
    signature = base64.b64decode(cred.sign('nonce-123'))
    assert user.verify(b'nonce-123', signature)
    assert 2000 < cred.renew_after(now) < 2200  # 60% of the remaining hour


@pytest.mark.asyncio
async def test_join_rejects_refusal_and_owner_change():
    text, _ = creds(time.time() + 3600)
    owners = iter(['f_0123456789abcdef', 'f_fedcba9876543210'])
    cred = FleetKeyCredential('https://fleet.example', KEY, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'fleet_id': next(owners), 'creds': text})))
    await cred.join()
    with pytest.raises(ValueError, match='owner'):
        await cred.join()
    refused = FleetKeyCredential('https://fleet.example', KEY, transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(ValueError, match='403'):
        await refused.join()
