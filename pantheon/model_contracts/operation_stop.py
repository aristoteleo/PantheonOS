"""Directory invariants for explicitly stopping an unfinished model operation."""
from .errors import DirectoryError


PENDING = ('recovery', 'connector_update', 'engine_update')


def require(condition):
    if not condition:
        raise DirectoryError(409, 'Stop only the exact pinned operation; retain its configuration and owned instances')


def same_identity(left, right):
    return {k: v for k, v in left.items() if k != 'generation'} == {
        k: v for k, v in right.items() if k != 'generation'}


def validate_transition(old, new):
    before, after = old.get('operation_stop'), new.get('operation_stop')
    if not before and not after:
        require(old.get('last_operation_stop') == new.get('last_operation_stop'))
        return False
    intent = before or after
    kind = intent['kind']
    require(bool(old.get(kind)) and sum(bool(old.get(k)) for k in PENDING) == 1)
    for field in ('name', 'node_name', 'models', 'config_revision'):
        require(old.get(field) == new.get(field))
    if after:
        # Only the stop intent may change while stopping. Old coordinators cannot
        # publish a late success or resume the abandoned startup plan.
        for field in (*PENDING, 'binding', 'engine_binding', 'managed', 'state', 'last_operation_stop'):
            require(old.get(field) == new.get(field))
        if before:
            require(before == after)
        targets = {t['role']: t for t in after['targets']}
        require(len(targets) == len(after['targets']) and 'binding' in targets)
        require(('engine_binding' in targets) == bool(old.get('engine_binding')))
        for role, target in targets.items():
            binding = target['binding']
            scope = 'model-' if role == 'binding' or (role == 'replacement' and kind == 'connector_update') else 'engine-'
            require(target['scope'] == scope + old['deployment_id'] and binding['node_id'] == old['node_id'])
            if role == 'replacement':
                require(kind != 'recovery')
                pending = old[kind]
                require(binding['revision'] == pending['target_revision'] and binding['generation'] in {0, 1})
                if kind == 'engine_update':
                    require(binding['instance_id'] == pending['target_instance_id'])
                else:
                    require(binding['instance_id'] != old['binding']['instance_id'])
            else:
                source = old[role]
                require(same_identity(binding, source))
                allowance = 2 if kind == 'recovery' else 1
                require(source['generation'] <= binding['generation'] <= source['generation'] + allowance)
        return True
    require(new['state'] == 'stopped' and all(not new.get(k) for k in PENDING))
    history = new.get('last_operation_stop') or {}
    require({k: v for k, v in history.items() if k != 'completed_at'} == before)
    require(history.get('completed_at', -1) >= before['started_at'])
    targets = {t['role']: t['binding'] for t in before['targets']}
    replacement = targets.get('replacement')
    managed = old.get('managed')
    if replacement and replacement['generation'] > 0:
        if kind == 'engine_update':
            targets['engine_binding'] = replacement
            managed = old[kind]['target_config']
        else:
            targets['binding'] = replacement
    require(new.get('managed') == managed)
    for role in ('binding', 'engine_binding'):
        target, result = targets.get(role), new.get(role)
        if not target:
            require(result is None)
        else:
            require(bool(result) and same_identity(result, target)
                    and result['generation'] in {target['generation'], target['generation'] + 1})
    return True
