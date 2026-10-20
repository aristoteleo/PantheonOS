"""Wake the chosen service before freezing its config and inference transport."""
import asyncio


async def observe(client, row, action='status'):
    policy = row.get('engine_idle') or {}
    if row['state'] != 'ready' or policy.get('phase') != 'enabled':
        raise ValueError('This service has no enabled engine idle policy')
    result = await client.hub_request('POST', '/api/model-services/' + row['deployment_id'] + '/engine-idle',
                                    {'action': action, 'revision': row['revision']})
    current, idle = result['deployment'], result['idle']
    if (current['state'] != 'ready' or any(current.get(k) for k in
            ('recovery', 'connector_update', 'engine_update', 'operation_stop'))
            or any(current.get(k) != row.get(k) for k in
                   ('deployment_id', 'node_id', 'engine', 'mode', 'binding', 'managed', 'engine_idle'))
            or idle['id'] != row['deployment_id'] or idle['revision'] != policy['policy_revision']
            or idle['enabled'] is not True or idle['state'] not in
                   {'active', 'fencing', 'stopping', 'sleeping', 'waking', 'rebinding'}):
        raise ValueError('Model idle policy changed; no inference was submitted')
    return current, idle


async def wake(client, row):
    policy = row.get('engine_idle') or {}
    if not policy or policy['phase'] == 'disabled':
        return row
    if policy['phase'] != 'enabled':
        raise ValueError('Finish changing the engine idle policy before calling this service')
    # No prompt, endpoint or management method is sent through this control path.
    # A timeout only stops waiting; it never creates a second start operation.
    try:
        async with asyncio.timeout(610):
            current, idle = await observe(client, row, 'wake')
            while idle['state'] != 'active':
                await asyncio.sleep(.5)
                current, idle = await observe(client, row)
                if idle['state'] == 'active':
                    # Refresh the admission grace and publish the verified new
                    # config/generation via Hub CAS before obtaining any grant.
                    current, idle = await observe(client, row, 'wake')
            if (current['engine_binding']['generation'] != idle['engine']['generation']
                    or current['config_revision'] != idle['config_revision']):
                raise ValueError('The awakened engine binding has not been published')
            return current
    except TimeoutError as exc:
        raise RuntimeError('The model engine is still waking. Inspect its status before retrying; no inference was submitted.') from exc
