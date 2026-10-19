"""Pure immutable leader identity and durable inference barriers.

Kept byte-identical in Hub, and frozen into rank packages. No I/O, credentials,
model execution or state inference from elapsed time occurs here.
"""
from copy import deepcopy
import hashlib
import json


FLAGS = ('activation_sent', 'activated', 'drain_sent', 'drained')


def configuration(topology, identity, context_length, parallel):
    return dict(engine='sglang', endpoint='http://127.0.0.1:30000/v1', credential_file='',
        group=deepcopy(topology), instance=deepcopy(identity), context_length=context_length, parallel=parallel)


def original(row, context_length, parallel):
    topology = row['peer_security']['topology']
    peer = topology['members'][0]
    target = next(m['target'] for m in row['members'] if m['target']['node_id'] == peer['node_id'])
    identity = dict(instance_id=hashlib.sha256('\0'.join((row['owner'], target['node_id'],
        target['digest'], target['scope'])).encode()).hexdigest()[:32], generation=peer['generation'])
    config = configuration(topology, identity, context_length, parallel)
    return dict(protocol=1, binding=dict(node_id=peer['node_id'], **identity, revision=target['digest'],
        component='backend', port='http'), context_length=context_length, parallel=parallel,
        config_revision=hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())


def intent(row, context_length, parallel):
    return {**original(row, context_length, parallel), **dict.fromkeys(FLAGS, False)}


def validate(row, *, initial=False):
    value = row.get('inference')
    if value is None:
        if 'inference' in row:
            raise ValueError('Omit absent inference intent; null cannot replace it')
        return None
    if (not isinstance(value, dict) or set(value) != {'protocol', 'binding', 'context_length',
            'parallel', 'config_revision', *FLAGS} or type(value['protocol']) is not int or value['protocol'] != 1
            or type(value['context_length']) is not int or not 512 <= value['context_length'] <= 1048576
            or type(value['parallel']) is not int or not 1 <= value['parallel'] <= 16
            or not isinstance(value['binding'], dict)
            or type(value['binding'].get('generation')) is not int
            or any(type(value[key]) is not bool for key in FLAGS)
            or not row.get('peer_security') or not row['peer_security'].get('network')):
        raise ValueError('Inference requires an exact leader configuration and original private network')
    expected = original(row, value['context_length'], value['parallel'])
    if any(value[key] != val for key, val in expected.items()):
        raise ValueError('Inference identity differs from the original leader')
    if (value['activated'] and not value['activation_sent']
            or value['activation_sent'] and not all(m['start']['sent'] for m in row['members'])
            or value['activation_sent'] and row['phase'] == 'preparing'
            or (value['drain_sent'] or value['drained']) and row['phase'] not in {'aborting', 'stopped', 'forgotten'}
            or row['phase'] == 'stopped' and not value['drained']):
        raise ValueError('Inference barriers are inconsistent with group lifecycle')
    if initial and any(value[key] for key in FLAGS):
        raise ValueError('Create inactive unacknowledged inference intent')
    if not value['drained'] and (row['peer_security']['closed']
            or row['peer_security']['network']['closed'] or any(m['stop'] for m in row['members'])):
        raise ValueError('Drain the original leader before closing any group peer or network')
    return value


def transition(old, new):
    a, b = old.get('inference'), validate(new)
    if (a is None) != (b is None):
        raise ValueError('Inference cannot be added to or removed from an existing group')
    if a is None:
        return
    if (any(a[key] != b[key] for key in a if key not in FLAGS)
            or any(a[key] and not b[key] for key in FLAGS)):
        raise ValueError('Original leader and acknowledged inference barriers are immutable')
    if not a['activation_sent'] and b['activation_sent']:
        if (old['phase'] != 'ready' or new['phase'] != 'ready'
                or not all((m.get('observation') or {}).get('state') == 'ready' for m in new['members'])):
            raise ValueError('Observe every original rank ready before claiming activation')
    if not a['activated'] and b['activated']:
        if not a['activation_sent'] or old['phase'] != 'ready' or new['phase'] != 'ready':
            raise ValueError('Persist activation claim before acknowledgement')
    if not a['drain_sent'] and b['drain_sent'] and old['phase'] != 'aborting':
        raise ValueError('Withdraw publication durably before claiming drain')
    if not a['drained'] and b['drained']:
        if old['phase'] != 'aborting' or (a['activation_sent'] and not a['drain_sent']):
            raise ValueError('Persist shutdown and drain claims before acknowledging cleanup')
    # A worker using an older journal revision cannot tear down the network in
    # the same commit which first records drain completion.
    if not a['drained'] and (new['peer_security']['closed']
            or new['peer_security']['network']['closed'] or any(m['stop'] for m in new['members'])):
        raise ValueError('Persist leader drain completion before group teardown')


def deployment(row):
    """Read-only projection, withdrawn atomically with group phase changes."""
    value = validate(row)
    if value is None:
        return None
    ready = row['phase'] == 'ready' and value['activated'] and not value['drained']
    model = 'fleet-snapshot-' + row['peer_security']['topology']['model_sha256']
    return dict(deployment_id='group-' + hashlib.sha256(row['group_id'].encode()).hexdigest()[:24],
        name=row['group_id'], node_id=value['binding']['node_id'], node_name=value['binding']['node_id'],
        engine='sglang', mode='group', group_id=row['group_id'],
        group_nodes=[p['node_id'] for p in row['peer_security']['topology']['members']],
        state='ready' if ready else 'stopped' if row['phase'] in {'stopped', 'forgotten'} else
              'stopping' if row['phase'] == 'aborting' else 'recovering',
        binding=deepcopy(value['binding']), config_revision=value['config_revision'], revision=row['revision'],
        models=[dict(id=model, name=model, operations=['text'], context=value['context_length'], compute='node',
            tools=None, vision=None, reasoning=None, structured_output=None)] if ready else [])
