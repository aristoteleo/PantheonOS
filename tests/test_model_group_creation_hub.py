"""Real Hub persistence + Runtime creation boundary; no engines or GPUs.

Run with the Hub checkout on PYTHONPATH and its test dependencies installed.
Authority delivery/build are deliberately controlled doubles for race injection;
this does not claim installed Fleet or distributed network acceptance.
"""
import asyncio
from copy import deepcopy
import datetime
import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio

pytest.importorskip('pantheon_hub', reason='Cross-repository Hub acceptance needs its checkout')
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from pantheon_hub.api.fleet import _fleet_id_for_user
from pantheon_hub.api.model_services import create_model_services_router
from pantheon_hub.api.model_group_creation import LaunchPlan
from pantheon_hub.core.database import Base, User
from pantheon_hub.core.security import create_access_token
from pantheon.models.client import ModelServices
from pantheon.models.group_creation import CreationCoordinator, CreationJournal, topology_for
from pantheon.models.group_journal import GroupConflict
from test_model_sglang_group import plan as original_plan, record, group
from test_model_group_package import prepared


def plan():
    value = original_plan()
    value['owner'] = _fleet_id_for_user('alice')
    for member in value['members']:
        member['generation'] = 2
    return value


GROUP = 'sglang-group'


@pytest_asyncio.fixture
async def durable(tmp_path, monkeypatch):
    import pantheon_hub.utils.auth as auth
    monkeypatch.setattr(auth, 'db_user_to_pydantic', lambda u: SimpleNamespace(id=u.id, username=u.username))
    state = SimpleNamespace(engine=None, client=None, rebuilds=0, lose_write=False)
    config = SimpleNamespace(session_secret_key='creation-test-only-at-least-32-characters')

    async def reopen():
        if state.client:
            await state.client.aclose()
        if state.engine:
            await state.engine.dispose()
        state.engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'hub.db'))
        session = async_sessionmaker(state.engine, expire_on_commit=False)
        async with state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session() as db:
            if not await db.get(User, 'alice'):
                db.add(User(id='alice', username='alice', email='alice@example.test'))
                await db.commit()

        class DB:
            SessionLocal = session
            async def get_user_by_id(self, db, uid):
                return await db.get(User, uid)

        app = FastAPI()
        app.include_router(create_model_services_router(config, DB()))
        token = create_access_token({'user_id': 'alice', 'scope': 'fleet'}, config.session_secret_key)
        state.client = ModelServices(hub='https://hub.test', token=token, transport=httpx.ASGITransport(app=app))

        class Transport:
            async def hub_request(self, method, path, data=None):
                result = await state.client.hub_request(method, path, data)
                if method in {'PUT', 'POST'} and state.lose_write:
                    state.lose_write = False
                    raise TimeoutError('Hub committed but reply was lost')
                return result

        state.rebuilds += 1
        return CreationJournal(Transport(), _fleet_id_for_user('alice'))

    state.reopen = reopen
    try:
        yield state
    finally:
        if state.client:
            await state.client.aclose()
        if state.engine:
            await state.engine.dispose()


class Authority:
    """Stable test CA; production node authority has separate Go acceptance."""
    def __init__(self):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Creation test CA')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(minutes=10))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), True).sign(key, hashes.SHA256()))
        self.pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        self.pin = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
        self.calls, self.closed, self.lose_reply = [], False, False
        self.entered, self.release = asyncio.Event(), None

    async def group_authority(self, node, action, **kwargs):
        self.calls.append((node, action, deepcopy(kwargs)))
        peers = topology_for(plan())
        if action == 'prepare':
            assert kwargs['topology'] == peers.document()
        else:
            assert action == 'close'
            assert kwargs == dict(group_id=GROUP, topology_sha256=peers.fingerprint)
            self.closed = True
        result = dict(protocol=1, owner=plan()['owner'], node_id=node, group_id=GROUP,
            topology_sha256=peers.fingerprint, state='closed' if self.closed else 'open',
            ca_pem=self.pem, ca_sha256=self.pin)
        if action == 'prepare':
            self.entered.set()
            if self.release:
                await self.release.wait()
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError('Node committed but reply was lost')
        return result


@pytest.mark.asyncio
async def test_lost_creation_and_claim_acks_rebuild_without_rpc(durable):
    journal = await durable.reopen()
    authority = Authority()
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await journal.create(plan(), 'd'*64)
    journal = await durable.reopen()
    assert (await journal.load(GROUP))['phase'] == 'intent'
    with pytest.raises(GroupConflict):
        await journal.create(plan(), 'd'*64)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await CreationCoordinator(journal, authority).advance(GROUP)
    assert not authority.calls
    journal = await durable.reopen()
    assert (await journal.load(GROUP))['authority_requested']
    # Retry the original CA request after a lost node reply, retaining its root.
    authority.lose_reply = True
    with pytest.raises(TimeoutError):
        await CreationCoordinator(journal, authority).advance(GROUP)
    journal = await durable.reopen()
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await CreationCoordinator(journal, authority).advance(GROUP)
    journal = await durable.reopen()
    row = await CreationCoordinator(journal, authority).advance(GROUP)
    assert row['ca_sha256'] == authority.pin
    assert len(authority.calls) == 2
    assert authority.calls[0] == authority.calls[1]
    assert await journal.list() == [row]
    assert durable.rebuilds == 5


@pytest.mark.asyncio
async def test_cancel_fences_late_authority_and_retries_only_close(durable):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    authority = Authority()
    controller = CreationCoordinator(journal, authority)
    await controller.advance(GROUP)
    authority.release = asyncio.Event()
    pending = asyncio.create_task(controller.advance(GROUP))
    await asyncio.wait_for(authority.entered.wait(), 2)
    try:
        await controller.stop(GROUP)
        authority.lose_reply = True
        with pytest.raises(TimeoutError):
            await controller.advance(GROUP)
        authority.release.set()
        with pytest.raises(GroupConflict):
            await pending
    finally:
        authority.release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    journal = await durable.reopen()
    row = await CreationCoordinator(journal, authority).advance(GROUP)
    assert row['phase'] == 'stopped' and row['authority_closed']
    assert row['ca_sha256'] is None and row['artifacts'] == []
    assert [action for _, action, _ in authority.calls] == ['prepare', 'close', 'close']
    assert await CreationCoordinator(journal, authority).advance(GROUP) == row
    assert len(authority.calls) == 3


@pytest.mark.asyncio
async def test_early_cancel_has_no_authority_effect(durable):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    authority = Authority()
    controller = CreationCoordinator(journal, authority)
    await controller.stop(GROUP)
    row = await controller.advance(GROUP)
    assert row['phase'] == 'stopped' and not authority.calls


@pytest.mark.asyncio
async def test_packages_resume_exact_ranks_and_cancel_discards_late_result(durable):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    authority, builds = Authority(), []

    async def build(row, rank):
        builds.append((row['source_sha256'], row['plan'], rank))
        return str(rank)*64

    controller = CreationCoordinator(journal, authority, builder=build)
    await controller.advance(GROUP)
    await controller.advance(GROUP)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await controller.advance(GROUP)
    journal = await durable.reopen()
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(row, rank):
        assert rank == 1
        entered.set()
        await release.wait()
        return await build(row, rank)

    controller = CreationCoordinator(journal, authority, builder=delayed)
    pending = asyncio.create_task(controller.advance(GROUP))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await controller.stop(GROUP)
        release.set()
        with pytest.raises(GroupConflict):
            await pending
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    row = await controller.advance(GROUP)
    assert row['phase'] == 'stopped'
    assert row['artifacts'] == [dict(rank=0, digest='0'*64)]
    assert builds == [('d'*64, plan(), 0), ('d'*64, plan(), 1)]


@pytest.mark.asyncio
async def test_completed_build_and_timeout_have_no_restart(durable):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    authority, canceled = Authority(), asyncio.Event()

    async def hanging(row, rank):
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    controller = CreationCoordinator(journal, authority, builder=hanging, timeout=.02)
    await controller.advance(GROUP)
    await controller.advance(GROUP)
    with pytest.raises(TimeoutError):
        await controller.advance(GROUP)
    assert canceled.is_set() and (await journal.load(GROUP))['artifacts'] == []

    async def build(row, rank):
        return str(rank)*64

    controller.builder = build
    await controller.advance(GROUP)
    row = await controller.advance(GROUP)
    assert row['phase'] == 'built'
    controller.builder = hanging
    assert await controller.advance(GROUP) == row
    assert len(authority.calls) == 1


def test_hub_runtime_and_engine_compiler_bind_identical_plan(group):
    value = plan()
    expected = topology_for(value).document()
    assert LaunchPlan(**value).topology().model_dump() == expected
    for rank in range(2):
        assert group.rank_launch(value, record(), rank, [24 << 30])['topology'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'pin', 'protocol', 'pem', 'closed'])
async def test_wrong_authority_cannot_enable_package_build(durable, change):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    authority = Authority()
    original = authority.group_authority

    async def wrong(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == 'owner': result['owner'] = 'f_' + 'f'*16
        elif change == 'pin': result['ca_sha256'] = 'f'*64
        elif change == 'protocol': result['protocol'] = True
        elif change == 'pem': result['ca_pem'] += result['ca_pem']
        elif change == 'closed': result['state'] = 'closed'
        return result

    async def forbidden(*args):
        pytest.fail('Cannot build with an unacknowledged original authority')

    authority.group_authority = wrong
    controller = CreationCoordinator(journal, authority, builder=forbidden)
    await controller.advance(GROUP)
    with pytest.raises(ValueError):
        await controller.advance(GROUP)
    row = await journal.load(GROUP)
    assert row['ca_sha256'] is None and row['artifacts'] == []


@pytest.mark.asyncio
async def test_changed_creation_ack_is_rejected(durable):
    journal = await durable.reopen()
    original = journal.client.hub_request

    async def altered(method, path, data=None):
        result = await original(method, path, data)
        if method == 'PUT':
            result['source_sha256'] = 'e'*64
        return result

    journal.client.hub_request = altered
    with pytest.raises(GroupConflict, match='exact creation'):
        await journal.create(plan(), 'd'*64)
    assert (await journal.load(GROUP))['source_sha256'] == 'd'*64


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['bool_protocol', 'bool_claim', 'bad_digest', 'duplicate_rank'])
async def test_malformed_journal_response_fails_before_rpc(durable, change):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    original = journal.client.hub_request

    async def altered(method, path, data=None):
        result = await original(method, path, data)
        if change == 'bool_protocol': result['protocol'] = True
        elif change == 'bool_claim': result['authority_requested'] = 1
        elif change == 'bad_digest': result['ca_sha256'] = 'not-a-digest'
        elif change == 'duplicate_rank': result['artifacts'] = [dict(rank=0, digest='d'*64)]*2
        return result

    journal.client.hub_request = altered
    authority = Authority()
    with pytest.raises(GroupConflict):
        await CreationCoordinator(journal, authority).advance(GROUP)
    assert not authority.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("with_network", [False, True])
async def test_real_rank_packages_survive_lost_hub_ack_and_agent_replacement(durable, tmp_path, prepared, with_network):
    from pantheon.models.group_package import GroupPackageStore
    journal = await durable.reopen()
    model, value = prepared
    value['owner'] = journal.owner
    if with_network:
        value['underlay'] = ['192.168.20.10:18441', '192.168.20.11:18441']
        for member in value['members']:
            member['interface'] = 'wg0'
    store = GroupPackageStore(tmp_path / 'durable-packages')
    source = store.capture(model)
    await journal.create(value, source)
    authority = Authority()

    async def prepare(node, action, **kwargs):
        assert action == 'prepare' and kwargs['topology'] == topology_for(value).document()
        return dict(protocol=1, owner=value['owner'], node_id=node, group_id=GROUP,
            topology_sha256=topology_for(value).fingerprint, state='open',
            ca_pem=authority.pem, ca_sha256=authority.pin)

    controller = CreationCoordinator(journal, SimpleNamespace(group_authority=prepare), builder=store)
    await controller.advance(GROUP)  # durable claim
    await controller.advance(GROUP)  # original root
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await controller.advance(GROUP)  # real rank0 persisted, response lost
    journal = await durable.reopen()
    original = (await journal.load(GROUP))['artifacts'][0]
    rebuilt = GroupPackageStore(store.root)
    row = await CreationCoordinator(journal, SimpleNamespace(group_authority=prepare), builder=rebuilt).advance(GROUP)
    assert row['phase'] == 'built' and len(row['artifacts']) == 2
    assert row['artifacts'][0] == original
    for artifact in row['artifacts']:
        assert hashlib.sha256(rebuilt.artifact(artifact['digest'])).hexdigest() == artifact['digest']
    assert len(list(store.root.glob('source-*'))) == 1
    assert len(list(store.root.glob('artifact-*'))) == 2

    # The same real archives are transferred atomically, even if the Hub response
    # disappears. No authority call or engine submission accompanies handoff.
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await journal.handoff(GROUP)
    journal = await durable.reopen()
    transferred = await journal.handoff(GROUP)
    assert transferred['creation']['phase'] == 'handed_off'
    assert [m['target']['digest'] for m in transferred['group']['members']] == [a['digest'] for a in row['artifacts']]
    assert all(not m['prepare']['sent'] and not m['start']['sent'] for m in transferred['group']['members'])
    assert transferred['group']['revision'] == 1
    assert all(('install' in m) == with_network for m in transferred['group']['members'])
    if with_network:
        assert all(not m['install']['sent'] and not m['install']['staged']
                   and m['install']['request']['digest'] == m['target']['digest']
                   for m in transferred['group']['members'])
    controller = CreationCoordinator(journal, SimpleNamespace(group_authority=prepare), builder=rebuilt)
    assert await controller.advance(GROUP) == transferred['creation']
    # Creation stop delegates only a durable abort; it must not close authority
    # while the lifecycle journal may still own reserved/running ranks.
    assert await controller.stop(GROUP) == transferred['creation']
    advanced = await journal.handoff(GROUP)
    assert advanced['group']['phase'] == 'aborting'
    assert advanced['group']['revision'] == 2
    assert advanced['creation'] == transferred['creation']


async def build_intent(durable):
    journal = await durable.reopen()
    await journal.create(plan(), 'd'*64)
    authority = Authority()
    async def builder(row, rank):
        return ('a' if rank == 0 else 'b')*64
    controller = CreationCoordinator(journal, authority, builder=builder)
    for _ in range(4):
        await controller.advance(GROUP)
    assert (await journal.load(GROUP))['phase'] == 'built'
    return journal, authority


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['artifact', 'operation', 'ca', 'source', 'revision'])
async def test_altered_handoff_ack_rejected(durable, change):
    journal, authority = await build_intent(durable)
    original = journal.client.hub_request
    async def altered(method, path, data=None):
        result = await original(method, path, data)
        if method == 'POST':
            if change == 'artifact': result['group']['members'][0]['target']['digest'] = 'f'*64
            elif change == 'operation': result['group']['members'][0]['start']['request']['operation_id'] = 'different'
            elif change == 'ca': result['group']['peer_security']['ca_sha256'] = 'f'*64
            elif change == 'source': result['creation']['source_sha256'] = 'f'*64
            elif change == 'revision': result['creation']['revision'] += 1
        return result
    journal.client.hub_request = altered
    with pytest.raises(GroupConflict):
        await journal.handoff(GROUP)
    journal = await durable.reopen()
    actual = await journal.handoff(GROUP)
    assert actual['group']['phase'] == 'preparing'
    assert actual['group']['members'][0]['target']['digest'] == 'a'*64
    assert len(authority.calls) == 1


@pytest.mark.asyncio
async def test_cancel_after_handoff_lost_ack_never_uses_creation_cleanup(durable):
    journal, authority = await build_intent(durable)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await journal.handoff(GROUP)
    journal = await durable.reopen()
    controller = CreationCoordinator(journal, authority)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await controller.stop(GROUP)
    journal = await durable.reopen()
    await CreationCoordinator(journal, authority).stop(GROUP)
    result = await journal.handoff(GROUP)
    assert result['group']['phase'] == 'aborting' and result['group']['revision'] == 2
    assert not authority.closed and len(authority.calls) == 1


@pytest.mark.asyncio
async def test_runtime_network_coordinator_survives_real_hub_replacement(durable, monkeypatch):
    import test_model_group_overlay as overlay
    import test_model_group_security as secured
    import test_model_groups as fixture
    from pantheon.models.group_hub import HubGroupJournal
    from pantheon.models.group_coordinator import GroupCoordinator
    creation_journal = await durable.reopen()
    for module in (overlay, secured, fixture):
        monkeypatch.setattr(module, 'OWNER', creation_journal.owner)
    journal = HubGroupJournal(creation_journal.client, creation_journal.owner)
    await journal.create('test', fixture.targets(), peer_security={**secured.security(), 'network': overlay.network()})
    fleet = overlay.OverlayFleet()
    controller = GroupCoordinator(journal, fleet)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await controller.advance('test')  # roster committed but acknowledgement lost
    assert not fleet.calls and not any(action == 'pin' for _, action, _ in fleet.overlay_calls)
    restarted = await durable.reopen()
    journal = HubGroupJournal(restarted.client, restarted.owner)
    controller = GroupCoordinator(journal, fleet)
    row = await controller.advance('test')
    assert row['peer_security']['network']['ready'] and not fleet.calls
    await fixture.drive(controller, 'ready')
    await controller.stop('test')
    await fixture.drive(controller, 'stopped')
    row = await journal.load('test')
    assert row['peer_security']['network']['closed']
    assert len([request for _, request in fleet.calls if request['action'] == 'start']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_install', [False, True])
async def test_installation_coordinator_survives_hub_replacement(durable, monkeypatch, fail_install):
    import test_model_group_install as installing
    import test_model_groups as fixture
    from pantheon.models.group_hub import HubGroupJournal
    from pantheon.models.group_coordinator import GroupCoordinator
    creation = await durable.reopen()
    monkeypatch.setattr(fixture, 'OWNER', creation.owner)
    monkeypatch.setattr(fixture, 'DIGEST', installing.DIGEST)
    journal = HubGroupJournal(creation.client, creation.owner)
    await journal.create('test', fixture.targets(), install=True)
    fleet, packages = installing.InstallingFleet(), installing.Packages()
    controller = GroupCoordinator(journal, fleet, packages=packages)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await controller.advance('test')  # original staging ACK persisted, no install sent
    assert not fleet.calls
    creation = await durable.reopen()
    journal = HubGroupJournal(creation.client, creation.owner)
    controller = GroupCoordinator(journal, fleet, packages=packages)
    if fail_install:
        fleet.failed = {('node-a', 'install')}
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await controller.advance('test')  # original install IDs persisted, delivery missing
    original = deepcopy([m['install']['request'] for m in (await journal.load('test'))['members']])
    assert not fleet.calls
    creation = await durable.reopen()
    journal = HubGroupJournal(creation.client, creation.owner)
    controller = GroupCoordinator(journal, fleet, packages=packages)
    packages.missing = True
    fleet.lost = {('node-a', 'install'), ('node-b', 'install')}
    await fixture.drive(controller, 'stopped' if fail_install else 'ready')
    if not fail_install:
        await controller.stop('test')
        await fixture.drive(controller, 'stopped')
    assert len(packages.reads) == 2 and len(fleet.executions) == 2
    assert [m['install']['request'] for m in (await journal.load('test'))['members']] == original
    assert len([r for _, r in fleet.calls if r['action'] == 'start']) == (0 if fail_install else 2)
