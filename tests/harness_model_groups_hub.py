"""Real Hub API + Runtime client + Go NativeDriver boundary acceptance.

Invoked by TestPythonGroupCoordinatorNativeProcesses with both source trees on
PYTHONPATH and a Python environment containing Hub dependencies. No deployed
services, credentials, user data, model weights or GPU are touched.
"""
import asyncio
import sys
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from pantheon_hub.api.fleet import _fleet_id_for_user
from pantheon_hub.api.model_services import create_model_services_router
from pantheon_hub.core.database import Base, User
from pantheon_hub.core.security import create_access_token
import pantheon_hub.utils.auth as auth
from pantheon.models.client import ModelServices
from pantheon.models.group_hub import HubGroupJournal
from harness_model_groups import main


async def run(endpoint, targets, path, scenario, *, driver=main):
    original = auth.db_user_to_pydantic
    auth.db_user_to_pydantic = lambda u: SimpleNamespace(id=u.id, username=u.username)
    config = SimpleNamespace(session_secret_key='group-tests-only-secret-at-least-32-characters')
    owner = _fleet_id_for_user('alice')
    engine = client = None
    restarts = 0

    async def reopen():
        nonlocal engine, client, restarts
        if client:
            await client.aclose()
        if engine:
            await engine.dispose()
        # Replace both application objects and DB connections. Only the Hub
        # database persists; no Agent-local group file/cache survives reopening.
        engine = create_async_engine('sqlite+aiosqlite:///' + path)
        session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
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
        client = ModelServices(hub='https://hub.test', token=token, transport=httpx.ASGITransport(app=app))
        restarts += 1
        return HubGroupJournal(client, owner)

    try:
        await driver(endpoint, targets, path, scenario, journal_factory=reopen)
        journal = await reopen()
        restored = await journal.load('test')
        assert restored['phase'] == 'stopped'
        assert all(m['observation']['clean'] for m in restored['members'])
        assert await journal.list() == [restored]
        print(f'Hub/Agent objects rebuilt {restarts} times; original group retained at revision {restored["revision"]}')
    finally:
        if client:
            await client.aclose()
        if engine:
            await engine.dispose()
        auth.db_user_to_pydantic = original


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
