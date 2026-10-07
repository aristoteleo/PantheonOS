"""Resume the owner's startup preset after its node lost the Apps.

A cloud workspace node is replaced when idle; the new node reports every App
instance stopped without a stop operation. Those Apps are started again at
their current generation under a deterministic operation ID, so a retry after a
platform restart continues the same operation instead of starting another.

An App whose latest operation is a stop stays stopped: the owner stopped it, or
idle policy did (it then starts again on first use). Apps that depend on such an
App stay stopped too. Nothing is resumed while any App of the preset is still
live, failed or mid-operation: a partial or failed state is for the owner.
"""
import copy
import hashlib


def targets(recipe):
    """{name: app} for consumer Apps and model providers alike."""
    apps = dict(recipe['apps'])
    for name, item in (recipe.get('model_apps') or {}).items():
        apps[name] = item['app']
    return apps


def _references(value, found):
    if isinstance(value, dict):
        for key in ('$app', '$model'):
            if isinstance(value.get(key), str):
                found.add(value[key])
        for item in value.values():
            _references(item, found)
    elif isinstance(value, list):
        for item in value:
            _references(item, found)
    return found


def dependencies(recipe):
    """{name: provider names it references} within this recipe."""
    apps = targets(recipe)
    return {name: _references(app, set()) & (apps.keys() - {name}) for name, app in apps.items()}


def classify(recipe, states):
    """{name: 'lost' | 'stopped' | 'live' | 'missing'} for each preset App.

    states maps node_id to its lifecycle status. 'stopped' means the latest
    operation for that instance identity was a stop (owner or idle policy).
    """
    result = {}
    for name, app in targets(recipe).items():
        state = states[app['node_id']]
        same = [i for i in (state.get('instances') or {}).values()
                if i.get('digest') == app['revision'] and i.get('scope') == app['scope']]
        if not same:
            result[name] = 'missing'
            continue
        instance = max(same, key=lambda i: int(i.get('generation') or 0))
        if instance.get('state') != 'stopped' or instance.get('resources') or instance.get('reservations'):
            result[name] = 'live'
            continue
        operations = [o for o in (state.get('operations') or {}).values()
                      if (o.get('request') or {}).get('digest') == app['revision']
                      and (o.get('request') or {}).get('scope') == app['scope']]
        if any(o.get('state') in ('queued', 'running') for o in operations):
            result[name] = 'live'
            continue
        latest = max(operations, key=lambda o: o.get('updated_at') or '', default=None)
        stopped = latest is not None and latest['request'].get('action') == 'stop'
        result[name] = 'stopped' if stopped else 'lost'
    return result


def resume_set(recipe, states, *, everything=False):
    """The Apps to start again, or an empty set when nothing should resume.

    everything: the owner asked to start the preset, so stopped Apps start too.
    """
    status = classify(recipe, states)
    if everything:
        status = {name: 'lost' if s == 'stopped' else s for name, s in status.items()}
    if 'lost' not in status.values() or any(s in ('live', 'missing') for s in status.values()):
        return set()
    needs = dependencies(recipe)
    chosen = {name for name, s in status.items() if s == 'lost'}
    # A consumer cannot run without its providers; keep owner stops intact.
    changed = True
    while changed:
        changed = False
        for name in list(chosen):
            if not needs[name] <= chosen:
                chosen.discard(name)
                changed = True
    return chosen


def resume_recipe(recipe, states, *, everything=False):
    """A recipe starting only the lost Apps at their current generations, or None."""
    chosen = resume_set(recipe, states, everything=everything)
    if not chosen:
        return None
    spec = copy.deepcopy(recipe)
    generations = {}
    for name, app in targets(spec).items():
        if name not in chosen:
            continue
        state = states[app['node_id']]
        app['generation'] = max(int(i.get('generation') or 0)
                                for i in state['instances'].values()
                                if i.get('digest') == app['revision'] and i.get('scope') == app['scope'])
        generations[name] = app['generation']
    spec['apps'] = {name: app for name, app in spec['apps'].items() if name in chosen}
    if 'model_apps' in spec:
        spec['model_apps'] = {name: item for name, item in spec['model_apps'].items() if name in chosen}
        if not spec['model_apps']:
            # Its model consumers were dropped above; the rest is a plain deployment.
            spec.pop('model_apps')
            spec.pop('kind', None)
    # Same lost state, same operation: a restarted platform continues it.
    key = hashlib.sha256(repr(sorted(generations.items())).encode()).hexdigest()[:12]
    spec['operation_id'] = f"{recipe['operation_id'][:60]}-resume-{key}"
    return spec
