"""Upgrade or roll back the startup preset to another pinned release set.

A release update keeps everything the owner set up: configuration, App
topology, placement and model publications. Only package releases change. The
candidate recipe is the same setup composed against the new release set; any
other difference is refused (it is a setup change, not a release update).
Model provider releases follow their own Model Services update workflow.

Changed Apps start at generation 0 from data Fleet copied out of their stopped
source; the source generation and its data are retained, so a rollback starts
the original release again at exactly that generation. Candidate-only writes
stay with the candidate and are not merged back.
"""
import copy

from pantheon.apps.dependency_assembly import AssemblyError

from . import preset_resume


def _comparable(app):
    return {k: v for k, v in app.items() if k not in ('revision', 'generation')}


def release_changes(current, candidate):
    """{App name: candidate revision} for a pure release update, else refuse."""
    if (current['owner'] != candidate['owner'] or current.get('kind') != candidate.get('kind')
            or current['apps'].keys() != candidate['apps'].keys()
            or (current.get('model_apps') or {}).keys() != (candidate.get('model_apps') or {}).keys()):
        raise AssemblyError('This release changes the App topology; set the Agent up again instead')
    for name, item in (current.get('model_apps') or {}).items():
        other = candidate['model_apps'][name]
        if ({k: v for k, v in item.items() if k != 'app'} != {k: v for k, v in other.items() if k != 'app'}
                or _comparable(item['app']) != _comparable(other['app'])):
            raise AssemblyError('This release changes model publications; review them in Model Services')
        if item['app']['revision'] != other['app']['revision']:
            raise AssemblyError('Model provider releases update through Model Services')
    changes = {}
    for name, app in current['apps'].items():
        other = candidate['apps'][name]
        if _comparable(app) != _comparable(other):
            raise AssemblyError(f'This release changes the configuration of {name}; set the Agent up again instead')
        if app['revision'] != other['revision']:
            changes[name] = other['revision']
    if not changes:
        raise AssemblyError('The pinned release is already running')
    return changes


def retained_candidates(candidate, changes, states):
    """Changed Apps whose target release already holds data in their scope.

    Fleet keeps one data directory per (release, scope) and never overwrites a
    used one, so a release that ran before (then was rolled back) cannot be
    prepared again from a fresh copy of the current data.
    """
    apps = preset_resume.targets(candidate)
    return sorted(name for name in changes
                  if any(i.get('digest') == apps[name]['revision'] and i.get('scope') == apps[name]['scope']
                         for i in (states[apps[name]['node_id']].get('instances') or {}).values()))


def current_generations(recipe, states):
    """Each preset App's latest generation on its node (all must be stopped)."""
    generations = {}
    for name, app in preset_resume.targets(recipe).items():
        same = [i for i in (states[app['node_id']].get('instances') or {}).values()
                if i.get('digest') == app['revision'] and i.get('scope') == app['scope']]
        if not same:
            raise AssemblyError(f'{name} is not installed on its node; inspect it in Fleet')
        latest = max(same, key=lambda i: int(i.get('generation') or 0))
        if latest.get('state') != 'stopped' or latest.get('resources') or latest.get('reservations'):
            raise AssemblyError(f'{name} must be stopped before a release change')
        generations[name] = int(latest['generation'])
    return generations


def with_generations(recipe, generations, operation_id):
    """The recipe restarting each App at the given generation, under a new operation."""
    spec = copy.deepcopy(recipe)
    for name, app in preset_resume.targets(spec).items():
        app['generation'] = generations[name]
    spec['operation_id'] = operation_id
    return spec


def receipt(source, target, changes, source_generations):
    """What a rollback restores: the exact source release at its retained generation."""
    return {'protocol': 1, 'source': source, 'target': target, 'changes': dict(changes),
            'origins': {name: {'digest': source['apps'][name]['revision'], 'scope': source['apps'][name]['scope'],
                               'generation': source_generations[name]} for name in changes}}


def rollback_generations(record, states):
    """Generations restoring the receipt's source; changed Apps return to their retained ones."""
    source = record['source']
    generations = {}
    for name, app in preset_resume.targets(source).items():
        origin = record['origins'].get(name)
        if origin is not None:
            same = [i for i in (states[app['node_id']].get('instances') or {}).values()
                    if i.get('digest') == origin['digest'] and i.get('scope') == origin['scope']]
            if len(same) != 1 or same[0].get('generation') != origin['generation'] or same[0].get('state') != 'stopped':
                raise AssemblyError(f'The retained {name} generation changed; it cannot be rolled back exactly')
            generations[name] = origin['generation']
            continue
        target_app = preset_resume.targets(record['target'])[name]
        same = [i for i in (states[target_app['node_id']].get('instances') or {}).values()
                if i.get('digest') == target_app['revision'] and i.get('scope') == target_app['scope']]
        if not same:
            raise AssemblyError(f'{name} is not installed on its node; inspect it in Fleet')
        generations[name] = max(int(i.get('generation') or 0) for i in same)
    return generations
