"""Opt-in two-version Ollama upgrade through the real Fleet test supervisor.

The Go test owns the isolated root, ports, resources and cleanup. Only the
registry is local to this test; actual Hub transition rules have API tests.
Uses previously checksum-verified public archives and Qwen GGUF, with no
downloads during service startup or upgrade. Does not touch user engines.
"""
import asyncio
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

import httpx

from pantheon.apps.lifecycle import build_artifact
from pantheon.models import manager as coordinator, engine_upgrade, managed

ADDRESS, ROOT, CACHE = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps' / 'model-service'))
from artifacts import ArtifactCache, atomic_json, file_lock
from engines import EngineCache, recipe

OLD, NEW = 'ollama-0.34.1-darwin', 'ollama-0.34.2-darwin'


class Directory:
    path = ROOT / 'test-registry.json'

    async def deployments(self):
        return [json.loads(self.path.read_text())] if self.path.exists() else []

    async def deployment(self, name):
        row = (await self.deployments())[0]
        assert row['deployment_id'] == name
        return row

    async def save(self, row):
        current = await self.deployments()
        assert row['revision'] == (current[0]['revision'] if current else 0)
        row = {**row, 'revision': row['revision'] + 1}
        atomic_json(self.path, row)
        return deepcopy(row)


class LocalLifecycle:
    lose_start = False
    allow_stage = True

    def __init__(self, _): pass

    async def status(self, _): return await bridge('status')

    async def stage(self, node, directory):
        assert self.allow_stage, 'A resumed upgrade tried to stage a different package'
        payload, digest = build_artifact(directory, 'darwin-arm64')
        await bridge('stage', digest=digest, data=base64.b64encode(payload).decode())
        return digest

    async def submit(self, node, action, digest, **kwargs):
        request = dict(protocol=1, operation_id=kwargs.pop('operation_id', None) or uuid.uuid4().hex,
                       generation=0, action=action, digest=digest)
        request.update(kwargs)
        result = await bridge('submit', request=request)
        if action == 'start' and self.lose_start:
            LocalLifecycle.lose_start = False
            raise ConnectionError('Intentionally lost start acknowledgement')
        return result

    async def usage(self, node, method, **binding):
        return await bridge('usage', binding=binding)


async def bridge(action, **kwargs):
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(ADDRESS, json=dict(action=action, **kwargs))
        if response.status_code != 200: raise RuntimeError(response.text)
        return response.json()['result']


async def node(*args, **kwargs):
    return dict(name='Isolated acceptance', capability=dict(os='darwin', arch='arm64'))


def manager():
    result = coordinator.ModelServiceManager(client=Directory(), resolver=object())
    result.node = node
    async def rpc(binding, method, args=None):
        result = await bridge('rpc', binding=binding, method=method, args=args or {})
        if result.get('error'): raise RuntimeError(result['error'])
        return result
    result.rpc = rpc
    return result


async def job(manager, action, **kwargs):
    job_id = uuid.uuid4().hex
    await manager.model_operations('upgrade', 'submit', job_id=job_id, operation=action, **kwargs)
    for _ in range(240):
        result = await manager.model_operations('upgrade')
        current = next(j for j in result['jobs'] if j['job_id'] == job_id)
        if current['state'] != 'running':
            assert current['state'] == 'succeeded', current
            return
        await asyncio.sleep(.25)
    raise AssertionError('Model operation timeout')


async def inference(manager, model_id, request_id):
    row = await manager.client.deployment('upgrade')
    state = await LocalLifecycle(None).status('isolated-mac')
    endpoint = state['instances'][row['binding']['instance_id']]['resources'][0]['endpoints']['http']
    started = time.monotonic()
    text, first = '', None
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream('POST', endpoint + '/v1/chat/completions', headers={
            'X-Model-Config': row['config_revision'], 'X-Model-Request': request_id}, json={
            'model': model_id, 'messages': [{'role': 'user', 'content': 'What is 2 + 2? Answer briefly.'}],
            'stream': True, 'max_tokens': 32, 'temperature': 0}) as response:
            assert response.status_code == 200, await response.aread()
            async for line in response.aiter_lines():
                if line == 'data: [DONE]': break
                if line.startswith('data: '):
                    payload = json.loads(line[6:])
                    assert not payload.get('error'), payload
                    for choice in payload.get('choices', []):
                        if content := choice.get('delta', {}).get('content'):
                            first = first or time.monotonic()
                            text += content
    assert '4' in text or 'four' in text.lower(), text
    print(json.dumps(dict(request=request_id, first_token_ms=round((first-started)*1000),
                         elapsed_ms=round((time.monotonic()-started)*1000))), flush=True)


async def main():
    coordinator.FleetLifecycle = engine_upgrade.FleetLifecycle = LocalLifecycle
    owned_cache = ROOT / 'cache' / 'model-service'
    blobs = ArtifactCache(CACHE / 'blobs')
    # Strictly offline for preparation and weights; absent cache fails rather
    # than hiding a download in the measured startup/upgrade path.
    class Offline:
        def open(self, *args, **kwargs): raise AssertionError('Acceptance attempted a download')
    blobs.opener = Offline()
    for recipe_id in (OLD, NEW):
        EngineCache(owned_cache, blobs, file_lock, atomic_json, 'engine-upgrade').fetch(
            recipe(recipe_id)['source'], threading.Event(), lambda *args: None)
    artifact = json.loads((CACHE / 'qwen-source.json').read_text())
    original = blobs.fetch(artifact, threading.Event(), lambda *args: None)
    (owned_cache / 'blobs').mkdir(parents=True, exist_ok=True)
    weight = owned_cache / 'blobs' / artifact['sha256']
    os.link(original, weight)
    config = dict(recipe_id=OLD, context_length=4096, parallel=1, keep_alive_seconds=300,
                  resources=dict(memory_bytes=2 << 30, devices=[dict(id='apple-metal', backend='metal', memory_bytes=2 << 30, exclusive=False)]))
    service = manager()
    await service.create_managed('upgrade', 'Version upgrade acceptance', 'isolated-mac', config)
    await service.set_running('upgrade', True)
    await service.artifacts('upgrade', 'submit', job_id='weights', source=artifact)
    for _ in range(80):
        jobs = await service.artifacts('upgrade')
        if jobs['jobs'][0]['state'] == 'ready': break
        await asyncio.sleep(.25)
    assert jobs['jobs'][0]['state'] == 'ready', jobs
    await job(service, 'import', artifact_job_id='weights')
    models = await service.model_operations('upgrade')
    model_id = models['models'][0]['id']
    row = await service.client.deployment('upgrade')
    await service.publish('upgrade', [dict(id=model_id, name='Qwen acceptance', operations=['text'], compute='node')], row['revision'])
    await job(service, 'load', model_id=model_id)
    await inference(service, model_id, 'before-upgrade')
    before = await service.client.deployment('upgrade')
    state = await LocalLifecycle(None).status('isolated-mac')
    connector_resources = state['instances'][before['binding']['instance_id']]['resources']
    model_files = owned_cache / 'models' / 'ollama' / 'engine-upgrade'
    def fingerprint():
        return {str(p.relative_to(model_files)): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                for p in model_files.rglob('*') if p.is_file()}
    saved_files = fingerprint()
    assert saved_files
    LocalLifecycle.lose_start = True
    started = time.monotonic()
    try:
        await service.upgrade_engine('upgrade', NEW)
        raise AssertionError('Expected simulated lost acknowledgement')
    except ConnectionError:
        pass
    assert (await service.client.deployment('upgrade'))['engine_update']
    service = manager()  # New Agent/coordinator; resumes only the durable intent.
    LocalLifecycle.allow_stage = False
    after = await service.upgrade_engine('upgrade', NEW)
    print(json.dumps(dict(upgrade_ms=round((time.monotonic()-started)*1000), old_recipe=OLD,
                         new_recipe=after['managed']['recipe_id'], resumed_lost_start=True)), flush=True)
    assert after['binding'] == before['binding'] and after['models'] == before['models']
    assert after['managed'] == {**before['managed'], 'recipe_id': NEW}
    assert after['engine_update'] is None and after['state'] == 'ready'
    assert saved_files == fingerprint(), 'Cached model files were copied, changed or removed'
    state = await LocalLifecycle(None).status('isolated-mac')
    assert state['instances'][before['binding']['instance_id']]['resources'] == connector_resources
    old = state['instances'][before['engine_binding']['instance_id']]
    assert old['state'] == 'stopped' and not old.get('resources') and not old.get('reservations')
    await job(service, 'load', model_id=model_id)
    await inference(service, model_id, 'after-upgrade')
    activity = await service.activity('upgrade')
    assert len(activity['requests']) == 2 and activity['accepting']
    assert activity['active_calls'] == activity['queued_calls'] == 0
    LocalLifecycle.allow_stage = True
    assert await service.upgrade_engine('upgrade', NEW) == after
    await service.set_running('upgrade', False)
    # Restart keeps its installed revision even if packaging code/catalog changed.
    LocalLifecycle.allow_stage = False
    restarted = await service.set_running('upgrade', True)
    assert restarted['engine_binding']['revision'] == after['engine_binding']['revision']
    assert restarted['models'] == before['models']
    await job(service, 'load', model_id=model_id)
    await inference(service, model_id, 'after-restart')
    await service.set_running('upgrade', False)
    assert weight.exists() and fingerprint() == saved_files
    print('Verified two engine versions, retained files/config/history, resumed start, inference, no-op repeat and full cleanup', flush=True)


asyncio.run(main())
