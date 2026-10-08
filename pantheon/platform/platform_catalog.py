"""Offer the platform's model catalog to the owner's Agent through Model Services.

The startup preset publishes only the tier models on the platform-budget
Connector. The owner expects the same choice of platform models the original
picker offered, so after the preset runs the curated OpenRouter catalog
(newest per vendor, as the original picker groups it) is published on that
Connector too. Only models the Connector itself discovers are published, with
capabilities from the service or the labelled OpenRouter entry, and only
tool-capable text models (an Agent needs tools). Models the owner already
published, or withdrew from chat, are left exactly as they are.
"""

DEPLOYMENT = 'platform'


def curated_models():
    """The original picker's platform models, vendor-grouped and ordered."""
    from pantheon.utils import openrouter_catalog
    groups = openrouter_catalog.reorder_and_filter(openrouter_catalog.by_vendor())
    return list(dict.fromkeys(model for models in groups.values() for model in models))


def additions(manager, row, discovered, curated, suggestions, withdrawn=()):
    """Chat entries for curated models this Connector offers but has not published."""
    published = {m['id'] for m in row.get('models') or []}
    found = {m['id']: m for m in discovered.get('models') or []}
    entries = []
    for model in curated:
        if model in published or model in withdrawn or model not in found:
            continue
        try:
            entry = manager.chat_entry(model, found[model].get('reported'), suggestions.get(model),
                                       compute='provider')
        except ValueError:
            continue  # No stated context: the owner can publish it with a limit in Model Services.
        if entry.get('tools') is True and entry.get('operations') == ['text']:
            entries.append(entry)
    return entries


async def publish_curated(manager, curated, *, deployment_id=DEPLOYMENT, withdrawn=()):
    """Publish missing curated models on the platform Connector; returns how many."""
    from pantheon.models.model_metadata import suggest
    row = await manager.client.deployment(deployment_id)
    if row.get('engine') != 'api' or row.get('state') != 'ready' or row.get('mode', 'attached') != 'attached':
        return 0
    discovered = await manager.rpc(row['binding'], 'discover')
    found = {m['id'] for m in discovered.get('models') or []}
    published = {m['id'] for m in row.get('models') or []}
    missing = [m for m in curated if m in found and m not in published and m not in withdrawn]
    if not missing:
        return 0
    entries = additions(manager, row, discovered, curated, await suggest(missing), withdrawn)
    if not entries:
        return 0
    await manager.publish(deployment_id, list(row.get('models') or []) + entries, row['revision'])
    return len(entries)



async def ensure_tier_routes(manager, tiers, *, deployment_id=DEPLOYMENT):
    """Create missing tier routes (tier-<name>) from the setup's model chains.

    A route that exists is the owner's: its models and order are kept. Only a
    platform Connector that moved to another node (a fresh setup elsewhere) is
    followed, so the route does not silently exclude every candidate.
    """
    from .first_run import TIER_ROUTE
    row = await manager.client.deployment(deployment_id)
    published = {m['id'] for m in row.get('models') or []}
    existing = {route['route_id']: route for route in await manager.client.routes()}
    changed = []
    for tier, chain in tiers.items():
        if not isinstance(chain, list):
            continue  # an older single-model tier setup has no route
        route_id = TIER_ROUTE.format(tier)
        route = existing.get(route_id)
        if route is None:
            candidates = [{'deployment_id': deployment_id, 'model_id': m} for m in chain if m in published]
            if not candidates:
                continue
            route = dict(route_id=route_id, name=tier.capitalize(), candidates=candidates,
                         allowed_nodes=[row['node_id']], allowed_compute=['provider'],
                         allowed_billing=['provider'], transport='relay_allowed', fallback='failover',
                         selection='ordered', requires={'operation': 'text', 'tools': True}, revision=0)
        elif route['name'] == f'{tier.capitalize()} quality':
            # Earlier seeds were named "<Tier> quality"; match the Agent's tier name.
            route = {**route, 'name': tier.capitalize()}
        elif (row['node_id'] not in route['allowed_nodes']
              and all(c['deployment_id'] == deployment_id for c in route['candidates'])):
            route = {**route, 'allowed_nodes': [row['node_id']]}
        else:
            continue
        await manager.client.route_operation('save', route=route)
        changed.append(route_id)
    return changed
