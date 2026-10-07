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

