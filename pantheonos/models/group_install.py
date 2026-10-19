"""Immutable rank installation intents, separate from process generations.

Staging is content-addressed and safe to repeat. Installation is submitted only
with its persisted operation ID; cancellation fences missing delivery at the
node and waits for accepted work. Installed packages remain reusable cache.
"""
import asyncio
import re


def validate_installs(row, *, initial=False):
    installs = [m.get('install') for m in row['members']]
    if not any(i is not None for i in installs):
        if any('install' in m for m in row['members']):
            raise ValueError('Omit absent installation intent')
        return False
    if any(i is None for i in installs):
        raise ValueError('Declare installation intent for every original rank')
    ids = []
    for member, intent in zip(row['members'], installs):
        if (not isinstance(intent, dict) or set(intent) != {'staged', 'sent', 'request'}
                or type(intent['staged']) is not bool or type(intent['sent']) is not bool
                or intent['sent'] and not intent['staged']):
            raise ValueError('Persist original staging acknowledgement before claiming install')
        target, request = member['target'], intent['request']
        expected = dict(protocol=1, action='install', digest=target['digest'], scope=target['scope'], generation=0)
        if (not isinstance(request, dict) or set(request) != set(expected) | {'operation_id'}
                or any(type(request.get(k)) is not type(v) or request[k] != v for k, v in expected.items())
                or not isinstance(request['operation_id'], str)
                or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', request['operation_id'])):
            raise ValueError('Install must retain its exact artifact, scope and operation ID')
        if member['prepare']['sent'] and not intent['sent']:
            raise ValueError('Install the original rank before preparing processes')
        if initial and (intent['staged'] or intent['sent']):
            raise ValueError('Create unacknowledged installation intent')
        ids.extend(member[k]['request']['operation_id'] for k in ('install', 'prepare', 'start', 'stop') if member.get(k))
    if len(set(ids)) != len(ids):
        raise ValueError('Every original group operation requires a distinct ID')
    return True


def validate_transition(old, new):
    validate_installs(new)
    for before, after in zip(old['members'], new['members']):
        a, b = before.get('install'), after.get('install')
        if (a is None) != (b is None):
            raise ValueError('Installation intent cannot be added or removed')
        if a is None:
            continue
        if a['request'] != b['request'] or any(a[k] and not b[k] for k in ('staged', 'sent')):
            raise ValueError('Installation identity and acknowledgements are immutable')
        if not a['staged'] and b['staged'] and (old['phase'] != 'preparing' or new['phase'] != 'preparing'):
            raise ValueError('Cancelled groups cannot resume artifact staging')
        if not a['sent'] and b['sent'] and (not a['staged'] or old['phase'] != 'preparing' or new['phase'] != 'preparing'):
            raise ValueError('Persist staging acknowledgement before submitting installation')
        if not before['prepare']['sent'] and after['prepare']['sent']:
            if (not a['sent'] or (after.get('observation') or {}).get('state') != 'installed'
                    or not all((m.get('observation') or {}).get('state') in {'installed', 'prepared'} for m in new['members'])):
                raise ValueError('Observe every original installation before preparing ranks')


async def stage_rank(lifecycle, packages, member):
    if packages is None:
        raise ValueError('Restore the original group package store before staging ranks')
    target = member['target']
    payload = await asyncio.to_thread(packages.artifact, target['digest'])
    actual = await lifecycle.stage_exact(target['node_id'], payload, target['digest'])
    if actual != target['digest']:
        raise ValueError('Staging did not acknowledge the original artifact digest')
    return True
