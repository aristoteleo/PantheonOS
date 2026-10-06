"""Explicit takeover of surviving, authenticated local infrastructure.

The separate management lock excludes live product owners. The inherited profile
lock still excludes replacement infrastructure. Receipt PIDs are never sufficient
authority: process birth identities, the original TLS CA and node registration
must agree before takeover. No App is started or replayed here.
"""
import asyncio
import hashlib
import json
import math
import os
import re

import httpx
import psutil

from pantheon.apps.owner_journal import OwnerJournal


def identity(process):
    return {'pid': process.pid, 'created': process.create_time(),
        'command_hash': hashlib.sha256(json.dumps(process.cmdline()).encode()).hexdigest()}


def process(value):
    if (not isinstance(value, dict) or set(value) != {'pid', 'created', 'command_hash'}
            or type(value['pid']) is not int or value['pid'] <= 1
            or type(value['created']) not in (float, int)
            or not math.isfinite(value['created']) or value['created'] <= 0
            or not isinstance(value['command_hash'], str) or not re.fullmatch('[a-f0-9]{64}', value['command_hash'])):
        raise RuntimeError('Invalid local process identity; inspect recovery')
    try:
        found = psutil.Process(value['pid'])
        if found.create_time() != value['created'] or not found.is_running() or found.status() == psutil.STATUS_ZOMBIE:
            return None
        if found.uids().effective != os.geteuid():
            raise RuntimeError('Local process belongs to another OS owner')
        if identity(found)['command_hash'] != value['command_hash']:
            raise RuntimeError('Local process executable or arguments changed')
        return found
    except psutil.NoSuchProcess:
        return None


class AttachedProcess:
    """Only signals the captured psutil birth identity, never a bare saved PID."""
    def __init__(self, child):
        self.child, self.pid = child, child.pid

    @property
    def returncode(self):
        try:
            return None if self.child.is_running() and self.child.status() != psutil.STATUS_ZOMBIE else 0
        except psutil.NoSuchProcess:
            return 0

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(.1)
        return self.returncode

    def terminate(self):
        try: self.child.terminate()
        except psutil.NoSuchProcess: pass

    def kill(self):
        try: self.child.kill()
        except psutil.NoSuchProcess: pass


async def checkpoint(runtime, phase):
    c = runtime.coordinates
    value = dict(protocol=1, phase=phase, workspace=str(runtime.workspace),
        owner=identity(psutil.Process()), children={name: identity(psutil.Process(child.pid))
            for name, child in runtime._children}, coordinates=None)
    if c is not None:
        value['coordinates'] = dict(controller=c.controller, nats=c.nats, fleet_id=c.fleet_id,
            node_id=c.node_id, ca_hash=hashlib.sha256(c.ca_certificate.read_bytes()).hexdigest())
    await OwnerJournal(runtime.root)._checkpoint(runtime.root/'processes.json', value)
    runtime._owns_process_receipt = True


async def adopt(runtime):
    from .local_fleet import LocalFleetCoordinates, _private_secret
    from .local_tls import trust_context
    journal = OwnerJournal(runtime.root)
    path = runtime.root/'processes.json'
    journal._private(path)
    with path.open('rb') as stream: raw = stream.read(16385)
    if len(raw) > 16384: raise RuntimeError('Local process receipt exceeds its bound')
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'protocol','phase','workspace','owner','children','coordinates'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or value['phase'] != 'ready' or value['workspace'] != str(runtime.workspace)
            or not isinstance(value['children'], dict) or set(value['children']) != {'controller','broker','runner'}):
        raise RuntimeError('Recovery requires the original ready infrastructure receipt')
    if process(value['owner']) is not None:
        raise RuntimeError('Original local product owner is still alive; use its stop control')
    children = {name: process(value['children'][name]) for name in ('controller','broker','runner')}
    if any(child is None for child in children.values()):
        raise RuntimeError('Only part of the local infrastructure survived; inspect recovery before replacing it')
    if len({child.pid for child in children.values()}) != 3 or os.getpid() in {child.pid for child in children.values()}:
        raise RuntimeError('Local infrastructure process identities are ambiguous')
    c = value['coordinates']
    if (not isinstance(c, dict) or set(c) != {'controller','nats','fleet_id','node_id','ca_hash'}
            or not isinstance(c['controller'], str) or not re.fullmatch(r'https://127\.0\.0\.1:[0-9]{1,5}', c['controller'])
            or not isinstance(c['nats'], str) or not re.fullmatch(r'nats://127\.0\.0\.1:[0-9]{1,5}', c['nats'])):
        raise RuntimeError('Invalid original local Fleet coordinates')
    ca = runtime.root/'tls-ca.pem'
    journal._private(ca)
    if hashlib.sha256(ca.read_bytes()).hexdigest() != c['ca_hash']:
        raise RuntimeError('Original local Fleet trust changed')
    for name in ('owner.key', 'service.key'): journal._private(runtime.root/name)
    key = _private_secret(runtime.root/'owner.key')
    fleet_id = 'f_' + hashlib.sha256(key.encode()).hexdigest()[:16]
    if c['fleet_id'] != fleet_id: raise RuntimeError('Original local Fleet owner changed')
    runtime._tls_context = trust_context(ca)
    async with httpx.AsyncClient(trust_env=False, timeout=5, verify=runtime._tls_context) as http:
        response = await http.get(c['controller']+'/nodes', params={'fleet': fleet_id},
            headers={'Authorization': 'Bearer '+_private_secret(runtime.root/'service.key')})
        response.raise_for_status()
        if not any(row.get('node_id') == c['node_id'] for row in response.json()['nodes']):
            raise RuntimeError('Original local Fleet node is no longer registered')
        expires = await runtime._issue_owner(http, c['controller'], c['nats'], fleet_id, key)
    # Recheck after network waits before assigning ownership or emitting signals.
    if any(process(value['children'][name]) is None for name in children):
        raise RuntimeError('Local infrastructure changed during recovery admission')
    value['owner'] = identity(psutil.Process())
    await journal._checkpoint(path, value)
    runtime._owns_process_receipt = True
    runtime._children = [(name, AttachedProcess(child)) for name, child in children.items()]
    runtime.coordinates = LocalFleetCoordinates(c['controller'], c['nats'], fleet_id, c['node_id'],
        runtime.root/'owner.creds', ca)
    runtime._renewal = asyncio.create_task(runtime._renew_owner(c['controller'], c['nats'], fleet_id, key, expires))
