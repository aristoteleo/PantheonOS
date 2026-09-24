"""Async Hub journal for group intent that survives Agent replacement.

No local fallback or implicit replay on a lost response. A commit with an unknown
acknowledgement is inspected by loading its original group ID on the next action.
"""
from copy import deepcopy
import re

from .group_journal import GroupConflict, GroupJournal
from .group_security import validate_security


class HubGroupJournal:
    def __init__(self, client, owner):
        if not isinstance(owner, str) or not re.fullmatch(r'f_[a-f0-9]{16}', owner):
            raise ValueError('The connected Fleet owner is required')
        self.client, self.owner = client, owner

    def path(self, group_id):
        if not isinstance(group_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', group_id):
            raise ValueError('Invalid group id')
        return '/api/model-services/groups/' + group_id

    def validate(self, row, group_id=None):
        if (row.get('protocol') != 1 or row.get('owner') != self.owner
                or (group_id is not None and row.get('group_id') != group_id)
                or type(row.get('revision')) is not int or row['revision'] < 1):
            raise GroupConflict('Hub returned a different group identity or protocol')
        validate_security(row)
        return row

    async def request(self, method, path, data=None):
        try:
            return await self.client.hub_request(method, path, data)
        except Exception as exc:
            # ControlError lives in the inference client; the journal itself
            # needs only the existing async Hub request interface.
            if getattr(exc, 'status', None) == 409:
                raise GroupConflict('Group changed; inspect its current durable intent') from exc
            if getattr(exc, 'status', None) == 404:
                raise KeyError(path) from exc
            raise

    async def list(self):
        result = await self.request('GET', '/api/model-services/groups')
        return [self.validate(row) for row in result['groups']]

    async def create(self, group_id, targets, *, peer_security=None):
        row = GroupJournal.plan(self.owner, group_id, targets, peer_security=peer_security)
        row['revision'] = 0
        return self.validate(await self.request('PUT', self.path(group_id), row), group_id)

    async def load(self, group_id):
        return self.validate(await self.request('GET', self.path(group_id)), group_id)

    async def save(self, row):
        self.validate(row)
        group_id = row['group_id']
        result = await self.request('PUT', self.path(group_id), deepcopy(row))
        self.validate(result, group_id)
        if result['revision'] != row['revision'] + 1:
            raise GroupConflict('Hub did not acknowledge this exact journal revision')
        return result
