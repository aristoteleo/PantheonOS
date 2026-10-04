"""Explicit legacy budget-state conversion using the existing Connector/vault.

The caller supplies the paired Hub provisioning receipt and confirmed browser
choice. No Hub login, billing database, model-ID rewrite or inference occurs here.
"""
from copy import deepcopy
import asyncio
from types import SimpleNamespace

from pantheon.models.credentials import model_credential_endpoint
from pantheon.models.platform_budget import budget_receipt

BUDGET_FIELDS = frozenset({'LLM_FORCE_PROXY', 'PLATFORM_MODEL_MODE',
    'PANTHEON_PLATFORM_PROXY_BASE', 'PANTHEON_PLATFORM_PROXY_KEY'})


def plan_budget(value, *, source, environment, vault):
    from .migration_models import validate_budget_choice
    if (source is None or not isinstance(value, dict)
            or set(value) != {'choice', 'provisioned'}):
        raise ValueError('Budget migration requires a runtime handoff, confirmed choice and provisioning receipt')
    choice = value['choice']
    if not isinstance(choice, dict):
        raise ValueError('Supply the confirmed source browser budget choice')
    choice = validate_budget_choice(choice, choice.get('service_id'))
    enabled = (environment.get('LLM_FORCE_PROXY') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    mode = (environment.get('PLATFORM_MODEL_MODE') or 'direct').strip().lower()
    if choice['enabled'] != enabled or mode not in ('direct', 'openrouter'):
        raise ValueError('Browser budget choice and captured runtime routing disagree; confirm the source before migration')
    base, key = (environment.get(name) for name in ('PANTHEON_PLATFORM_PROXY_BASE', 'PANTHEON_PLATFORM_PROXY_KEY'))
    receipt, entry = None, None
    if enabled or base or key:
        try:
            if not base or not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
                raise ValueError
            endpoint = model_credential_endpoint(base)
            # The paired Hub API explicitly supplies /v1 for its proxy_url.
            # Accept only that same prefix expansion, never a new host/path.
            if not endpoint.endswith('/v1'):
                endpoint += '/v1'
            received = value['provisioned']
            connector = {'engine': 'api', 'endpoint': endpoint,
                         'secret_ref': received['connector']['secret_ref']}
            receipt = budget_receipt(received, owner=vault.owner, node_id=vault.node_id, connector=connector)
            if receipt['model_mode'] != mode:
                raise ValueError
            entry = (connector['secret_ref'], connector['endpoint'], key)
        except (TypeError, KeyError, ValueError):
            raise ValueError('Captured budget credentials do not match the paired Hub provisioning receipt') from None
    elif value['provisioned'] is not None:
        raise ValueError('A disabled unconfigured source cannot acquire budget credentials during migration')
    return ({'source': source, 'choice': deepcopy(choice), 'model_mode': mode,
             'provisioning': receipt}, entry)


async def review_budget_models(client, receipt, references):
    """Pin all selected route candidates to the existing budget publication.

    Read-only directory review. Existing deployment preparation must still check
    these bindings/revisions before starting the candidate; this issues no grant.
    """
    from pantheon.apps.dependency_assembly import _copy
    from pantheon.models.dependency_plan import plan_dependency
    from pantheon.models.managed import module
    try:
        receipt = budget_receipt(receipt, owner=receipt['owner'], node_id=receipt['node_id'],
                                 connector=receipt['connector'])
        async with asyncio.timeout(30):
            rows = _copy(await client.deployments(), limit=4 * 1024 * 1024)
            routes = (_copy(await client.routes(), limit=1024 * 1024)
                      if any(ref.startswith('fleet-route://') for ref in references) else [])
        async def deployments(): return rows
        async def route_directory(): return routes
        review = await plan_dependency(SimpleNamespace(deployments=deployments, routes=route_directory),
                                       references=references, allow_wake=False)
        connector = module('server')
        expected = connector.configuration_revision(connector.validate_config(receipt['connector']))
        selected = {row['deployment_id']: row for row in rows}
        for identity, binding in review['policy']['deployments'].items():
            row = selected[identity]
            if (row['node_id'] != receipt['node_id'] or binding['node_id'] != receipt['node_id']
                    or row['engine'] != 'api' or row['config_revision'] != expected):
                raise ValueError
        if any('text' not in item['operations'] or item['capabilities']['tools'] is not True
               or type(item['context']) is not int or item['context'] <= 0 for item in review['selected']):
            raise ValueError
        return {'provisioning': receipt, 'model_selection': review}
    except Exception:
        raise ValueError('Selected models or route candidates do not match the provisioned budget Connector') from None
