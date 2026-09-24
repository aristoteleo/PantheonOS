"""Persist the original plan before authority or artifact construction.

This pre-launch workflow never installs or starts an engine. Atomic handoff
binds its built artifacts to the coordinated lifecycle journal. Fleet admission
remains mandatory before preparation. Reading a creation has no side effects.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import re
import ssl

from .group_hub import HubGroupJournal
from .group_journal import GroupConflict, GroupJournal
from .group_network import PeerTopology


def topology_for(plan):
    if 'underlay' in plan:
        from .group_network import addresses
        addresses(plan['underlay'], len(plan['members']))
    members = [dict(rank=m['rank'], node_id=m['node_id'], generation=m['generation'],
                    address=m['address'], port=m['control_port']) for m in plan['members']]
    launch = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return PeerTopology(dict(protocol=1, owner=plan['owner'], group_id=plan['group_id'],
        model_sha256=plan['model_sha256'], launch_sha256=launch, members=members))


class CreationJournal(HubGroupJournal):
    def path(self, group_id):
        return super().path(group_id).replace('/groups/', '/group-creations/')

    def validate(self, row, group_id=None):
        if (not isinstance(row, dict) or set(row) != {'protocol', 'owner', 'group_id', 'revision', 'plan', 'topology', 'source_sha256',
                        'phase', 'authority_requested', 'ca_sha256', 'authority_closed', 'artifacts'}
                or type(row['protocol']) is not int or row['protocol'] != 1 or row['owner'] != self.owner
                or (group_id is not None and row['group_id'] != group_id)
                or type(row['revision']) is not int or not 1 <= row['revision'] < 2**63-1
                or not isinstance(row['phase'], str)
                or row['phase'] not in {'intent', 'building', 'built', 'handed_off', 'aborting', 'stopped'}
                or any(type(row[key]) is not bool for key in ('authority_requested', 'authority_closed'))
                or not isinstance(row['source_sha256'], str)
                or not re.fullmatch('[a-f0-9]{64}', row['source_sha256'])
                or row['ca_sha256'] is not None and (not isinstance(row['ca_sha256'], str)
                    or not re.fullmatch('[a-f0-9]{64}', row['ca_sha256']))):
            raise GroupConflict('Hub returned a different creation identity or protocol')
        peers = topology_for(row['plan'])
        if (peers.document() != row['topology'] or row['plan']['owner'] != self.owner
                or row['plan']['group_id'] != row['group_id']):
            raise GroupConflict('Creation plan differs from its pinned topology')
        self.path(row['group_id'])
        artifacts = row['artifacts']
        if (not isinstance(artifacts, list) or len(artifacts) > len(peers.document()['members'])
                or any(not isinstance(a, dict) or set(a) != {'rank', 'digest'}
                    or type(a['rank']) is not int or not 0 <= a['rank'] < len(peers.document()['members'])
                    or not isinstance(a['digest'], str) or not re.fullmatch('[a-f0-9]{64}', a['digest'])
                    for a in artifacts)
                or [a['rank'] for a in artifacts] != sorted({a['rank'] for a in artifacts})):
            raise GroupConflict('Creation has invalid original rank artifacts')
        if row['phase'] in {'built', 'handed_off'} and (
                len(artifacts) != len(peers.document()['members']) or not row['ca_sha256']
                or not row['authority_requested'] or row['authority_closed']):
            raise GroupConflict('Incomplete or closed creation cannot transfer lifecycle ownership')
        return row

    async def handoff(self, group_id):
        row = await self.load(group_id)
        if row['phase'] not in {'built', 'handed_off'}:
            raise GroupConflict('Build all original rank packages before handoff')
        result = await self.request('POST', self.path(group_id) + '/handoff', {'revision': row['revision']})
        if not isinstance(result, dict) or set(result) != {'creation', 'group'}:
            raise GroupConflict('Hub returned an invalid handoff acknowledgement')
        creation = self.validate(result['creation'], group_id)
        expected = {**row, 'phase': 'handed_off',
                    'revision': row['revision'] + (row['phase'] != 'handed_off')}
        if creation != expected:
            raise GroupConflict('Hub did not transfer this exact built intent')
        group = HubGroupJournal(self.client, self.owner).validate(result['group'], group_id)
        original = lifecycle_plan(row)
        if (len(group['members']) != len(original['members'])
                or group['peer_security']['topology'] != row['topology']
                or group['peer_security']['ca_sha256'] != row['ca_sha256']
                or (group['peer_security'].get('network') or {}).get('addresses') != row['plan'].get('underlay')):
            raise GroupConflict('Lifecycle differs from original creation trust')
        for actual, pinned in zip(group['members'], original['members']):
            if (actual['target'] != pinned['target'] or any(
                    actual[key]['request'] != pinned[key]['request'] for key in ('prepare', 'start'))
                    or (actual.get('install') or {}).get('request') != (pinned.get('install') or {}).get('request')):
                raise GroupConflict('Lifecycle differs from original rank targets or operations')
        # A retried acknowledgement may contain an advanced/stopped group. Never
        # overwrite it with this initial plan or issue an implicit prepare/start.
        return result

    async def create(self, plan, source_sha256):
        if plan.get('owner') != self.owner:
            raise ValueError('Create only within the connected Fleet')
        row = dict(protocol=1, owner=self.owner, group_id=plan['group_id'], revision=0,
            plan=deepcopy(plan), topology=topology_for(plan).document(), source_sha256=source_sha256,
            phase='intent', authority_requested=False, ca_sha256=None, authority_closed=False, artifacts=[])
        # The Hub strictly validates the complete typed launch plan before commit.
        # No authority request can be sent by this method, even on a lost reply.
        result = self.validate(await self.request('PUT', self.path(plan['group_id']), row), plan['group_id'])
        if result != {**row, 'revision': 1}:
            raise GroupConflict('Hub did not acknowledge this exact creation intent')
        return result

    async def list(self):
        result = await self.request('GET', '/api/model-services/group-creations')
        return [self.validate(row) for row in result['creations']]


def lifecycle_plan(row):
    """Independent client check of Hub-derived immutable handoff identities."""
    targets = [dict(node_id=member['node_id'], digest=artifact['digest'],
                    scope='model-group-' + row['group_id'], generation=member['generation']-2)
               for member, artifact in zip(row['plan']['members'], row['artifacts'])]
    result = GroupJournal.plan(row['owner'], row['group_id'], targets,
        peer_security=dict(topology=row['topology'], ca_sha256=row['ca_sha256'], ready=False, closed=False),
        install='underlay' in row['plan'])
    if 'underlay' in row['plan']:
        result['peer_security']['network'] = dict(addresses=deepcopy(row['plan']['underlay']),
            endpoints=[], ready=False, closed=False)
    for rank, member in enumerate(result['members']):
        for action in (('install', 'prepare', 'start') if 'install' in member else ('prepare', 'start')):
            identity = [row['owner'], row['group_id'], row['source_sha256'], rank, action]
            member[action]['request']['operation_id'] = hashlib.sha256(
                json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        member['start']['request']['start_preparation_id'] = member['prepare']['request']['operation_id']
    return result


class CreationCoordinator:
    def __init__(self, journal, lifecycle, *, builder=None, timeout=15):
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError('Creation steps require a bounded deadline')
        self.journal, self.lifecycle, self.builder, self.timeout = journal, lifecycle, builder, timeout

    async def stop(self, group_id):
        row = await self.journal.load(group_id)
        if row['phase'] == 'handed_off':
            from .group_coordinator import GroupCoordinator
            journal = HubGroupJournal(self.journal.client, self.journal.owner)
            await GroupCoordinator(journal, self.lifecycle, rpc_timeout=self.timeout).stop(group_id)
            return row
        if row['phase'] not in {'aborting', 'stopped'}:
            row['phase'] = 'aborting'
            row = await self.journal.save(row)
        return row

    async def advance(self, group_id):
        row = await self.journal.load(group_id)
        if row['phase'] in {'built', 'handed_off', 'stopped'}:
            return row
        peers = PeerTopology(row['topology'])
        node = peers.member(0)['node_id']
        if row['phase'] == 'intent':
            row['phase'], row['authority_requested'] = 'building', True
            # Return after the durable claim; no RPC precedes its acknowledgement.
            return await self.journal.save(row)
        if row['phase'] == 'aborting':
            if row['authority_requested'] and not row['authority_closed']:
                result = await asyncio.wait_for(self.lifecycle.group_authority(node, 'close',
                    group_id=group_id, topology_sha256=peers.fingerprint), self.timeout)
                self._check_authority(row, peers, result, 'closed')
                row['authority_closed'] = True
            row['phase'] = 'stopped'
            return await self.journal.save(row)
        if not row['ca_sha256']:
            # Node Prepare is idempotent on this immutable topology, unlike an
            # engine start. A lost reply retries the same authority, never a new CA.
            result = await asyncio.wait_for(self.lifecycle.group_authority(node, 'prepare',
                topology=peers.document()), self.timeout)
            self._check_authority(row, peers, result, 'open')
            pem = result.get('ca_pem')
            if (not isinstance(pem, str) or not 0 < len(pem) <= 16384
                    or pem.count('-----BEGIN CERTIFICATE-----') != 1):
                raise ValueError('Missing original public authority certificate')
            digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            if result.get('ca_sha256') != digest:
                raise ValueError('Authority pin does not match its public certificate')
            row['ca_sha256'] = digest
            return await self.journal.save(row)
        if self.builder is None:
            return row
        known = {a['rank'] for a in row['artifacts']}
        for peer in peers.document()['members']:
            if peer['rank'] in known:
                continue
            # Builder must reproduce source_sha256 and return a content-addressed
            # artifact. It may not install, start or change the original plan.
            digest = await asyncio.wait_for(self.builder(deepcopy(row), peer['rank']), self.timeout)
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('Builder did not return an immutable artifact digest')
            row['artifacts'].append(dict(rank=peer['rank'], digest=digest))
            row['artifacts'].sort(key=lambda a: a['rank'])
            if len(row['artifacts']) == len(peers.document()['members']):
                row['phase'] = 'built'
            return await self.journal.save(row)
        raise GroupConflict('Creation has no remaining artifact step')

    @staticmethod
    def _check_authority(row, peers, result, state):
        expected = dict(protocol=1, owner=row['owner'], node_id=peers.member(0)['node_id'],
            group_id=row['group_id'], topology_sha256=peers.fingerprint, state=state)
        if not isinstance(result, dict) or any(type(result.get(k)) is not type(v) or result[k] != v for k, v in expected.items()):
            raise ValueError('Node returned another creation authority')
