"""Contract gate against the real Hub router/SQLite, not copied expected JSON.

Run with the paired Hub checkout on PYTHONPATH and its test dependencies. No
server, login, Fleet node, model or user database outside this test is touched.
"""
from types import SimpleNamespace

import pytest
pytest.importorskip('pantheon_hub', reason='Supply the paired Hub checkout and test environment')
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from pantheon_hub.api.model_services import create_model_services_router
from pantheon_hub.core.database import Base, User
from pantheon_hub.core.security import create_access_token
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.errors import ControlError
from test_local_model_directory import publication, alias


@pytest.mark.asyncio
async def test_local_attached_publication_and_alias_contract_matches_hub(tmp_path, monkeypatch):
    import pantheon_hub.utils.auth as auth
    monkeypatch.setattr(auth, 'db_user_to_pydantic', lambda u: SimpleNamespace(id=u.id, username=u.username))
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path/'hub.db'))
    session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session() as db:
        db.add(User(id='owner', username='owner', email='owner@example.test'))
        await db.commit()
    class DB:
        SessionLocal = session
        async def get_user_by_id(self, db, uid): return await db.get(User, uid)
    config = SimpleNamespace(session_secret_key='local-directory-contract-secret-at-least-32-characters')
    app = FastAPI()
    app.include_router(create_model_services_router(config, DB()))
    headers = {'Authorization': 'Bearer '+create_access_token({'user_id': 'owner', 'scope': 'fleet'}, config.session_secret_key)}
    directory = LocalModelDirectory(tmp_path/'local', owner='owner')
    await directory.initialize()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://hub.test', headers=headers) as hub:
            async def same(method, path, data=None):
                kwargs = {} if data is None else {'json': data}
                response = await hub.request(method, path, **kwargs)
                local = await directory.hub_request(method, path, data)
                assert response.status_code == 200, response.text
                assert local == response.json()
                return local
            for identity, kind, operations in (
                ('local', 'ollama', ['text']), ('remote', 'api', ['text', 'embedding', 'rerank']),
                ('lm', 'lmstudio', ['text']), ('voice', 'speaches', ['speech', 'transcription']),
                ('diffusion', 'sglang', ['image', 'video']),
            ):
                row = publication() | {'deployment_id': identity, 'engine': kind}
                row['models'][0]['operations'] = operations
                await same('PUT', '/api/model-services/'+identity, row)
            await same('GET', '/api/model-services')
            path = '/api/model-services/routes/preferred'
            route = alias()
            route['candidates'].append({'deployment_id': 'remote', 'model_id': 'model'})
            route['fallback'] = 'preflight'
            route = await same('PUT', path, route)
            await same('GET', '/api/model-services/routes')
            for request in ({}, {'tools': True}, {'vision': True}, {'context': 32768},
                            {'structured_output': True}, {'operation': 'image'}):
                await same('POST', path+'/resolve', request)
            for change in ({'allowed_compute': ['provider'], 'allowed_billing': ['provider']},
                           {'allowed_nodes': ['other']}, {'transport': 'direct_only'}, {'fallback': 'none'}):
                route = await same('PUT', path, route | change)
                await same('POST', path+'/resolve', {})
            # Both stores reject secrets and mismatched modality declarations.
            for change in ({'api_key': 'never-store'}, {'models': [{'id': 'x', 'operations': ['image']}]},
                           {'node_name': 'x'*121}, {'engine': 'speaches'}):
                bad = publication() | change
                response = await hub.put('/api/model-services/local', json=bad)
                assert response.status_code >= 400
                with pytest.raises(ControlError): await directory.save(bad)
            # CAS failures agree; neither store silently overwrites the winner.
            with pytest.raises(ControlError) as conflict:
                await directory.save(publication())
            assert conflict.value.status == 409
            assert (await hub.put('/api/model-services/local', json=publication())).status_code == 409
    finally:
        await engine.dispose()
