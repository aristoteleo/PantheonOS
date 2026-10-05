"""Attach an ordinary App consumer to an explicitly selected model-control App.

This only composes the existing deployment recipe. It issues no credentials,
starts no processes and selects no models. Shared consumers can bind a control
App whose lifetime is independent of Agent, Notebook or another client.
"""
import re

from pantheon.apps.dependency_assembly import AssemblyError, NAME, _copy, _matches, _identity
from pantheon.apps.deployment import deployment_recipe

_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


def bind_model_dependency(apps, *, consumer, provider, slot, policy_id, policy):
    """Return a copy with a generation-bound control grant and consumer policy.

    `provider` is an existing prepared model-services-control App in `apps`.
    Its owner-side credentials stay there. `policy` is the deployments/routes/
    wake selection from plan_dependency, or an explicit bootstrap selection
    containing exact {$model: alias} references. No existing policy or credential
    slot is replaced. The resulting recipe still needs normal owner admission.
    """
    apps, policy = _copy([apps, policy])
    try:
        if (not isinstance(apps, dict) or consumer == provider
                or any(not _matches(NAME, name) for name in (consumer, provider, slot))
                or not isinstance(policy_id, str) or not _ID.fullmatch(policy_id)
                or not isinstance(policy, dict) or set(policy) != {'deployments', 'routes', 'allow_wake'}
                or type(policy['allow_wake']) is not bool
                or not isinstance(policy['deployments'], dict) or len(policy['deployments']) > 64
                or not isinstance(policy['routes'], dict) or len(policy['routes']) > 128):
            raise ValueError
        for name, binding in policy['deployments'].items():
            if not _ID.fullmatch(name) or not isinstance(binding, dict):
                raise ValueError
            if set(binding) == {'$model'}:
                if not _matches(NAME, binding['$model']):
                    raise ValueError
            else:
                _identity(binding, provider=True)
                if binding['component'] != 'backend' or binding['port'] != 'http':
                    raise ValueError
        if any(not _ID.fullmatch(name) or type(revision) is not int or revision < 1
               for name, revision in policy['routes'].items()):
            raise ValueError
        target = apps[consumer]
        backend = target['components']['backend']
        control = apps[provider]['components']['backend']['values']['model_services']
        if (slot in target['bindings'] or slot in backend.get('credentials', {})
                or type(control['protocol']) is not int or control['protocol'] != 1
                or not isinstance(control['policies'], dict) or policy_id in control['policies']
                or len(control['policies']) >= 64):
            raise ValueError
        control['policies'][policy_id] = {'consumer': {'$app': consumer}, **policy}
        target['bindings'][slot] = {
            'app_id': 'model-services-control', 'component': 'backend',
            'provider': {'$app': provider, 'component': 'backend', 'port': 'http'},
            'methods': {'model_services_control': {
                'arguments': ['operation', 'arguments'], 'bound': {'policy_id': policy_id}}},
        }
        # Also reject cycles, unresolved App references and invalid exact targets.
        # Model bootstrap resolves $model later using the existing directory.
        deployment_recipe('composition-validation', 'model-binding', apps)
    except (KeyError, TypeError, ValueError, AttributeError, AssemblyError):
        raise AssemblyError('Supply distinct prepared model provider/consumer Apps and an unused scoped model policy') from None
    return apps


async def select_model_dependencies(client, *, apps, selections):
    """Review each ordinary consumer's model selection using the real directory.

    All graph/slot conflicts are checked before directory access. Selection does
    not save a preset, wake an engine, issue a grant or submit inference.
    """
    from .dependency_plan import plan_dependency
    apps, selections = _copy([apps, selections])
    if not isinstance(selections, dict) or len(selections) > 16:
        raise AssemblyError('Supply bounded model selections keyed by consumer App alias')
    preflight = apps
    for consumer, selection in selections.items():
        if (not isinstance(selection, dict) or set(selection) != {
                'provider', 'slot', 'policy_id', 'references', 'allow_wake'}
                or not isinstance(selection['references'], list) or not selection['references']
                or type(selection['allow_wake']) is not bool):
            raise AssemblyError('Supply model references, an explicit provider/slot/policy and a wake choice')
        preflight = bind_model_dependency(preflight, consumer=consumer, provider=selection['provider'],
            slot=selection['slot'], policy_id=selection['policy_id'],
            policy={'deployments': {}, 'routes': {}, 'allow_wake': selection['allow_wake']})
    reviews = {}
    for consumer, selection in selections.items():
        review = await plan_dependency(client, references=selection['references'], allow_wake=selection['allow_wake'])
        apps = bind_model_dependency(apps, consumer=consumer, provider=selection['provider'],
            slot=selection['slot'], policy_id=selection['policy_id'], policy=review['policy'])
        reviews[consumer] = review
    return {'apps': apps, 'model_selections': reviews}
