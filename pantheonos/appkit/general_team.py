"""The General Team's setup: the original Agent with platform-budget models.

Composed at release build into the deployment profile
(``release_profile.write_profile``); owners' choices fill its inputs.
"""
from urllib.parse import quote

WORKSPACE = '/workspace/default_workspace'

# Quality tiers are owner-editable Model Services routes (tier-<name>) the Agent
# follows with failover: the first model, then the next when a call fails. These
# chains seed new routes; an existing route keeps the owner's edits.
DEFAULT_TIERS = {
    'high': ['openrouter/anthropic/claude-sonnet-5.5', 'openrouter/anthropic/claude-sonnet-5',
             'openrouter/anthropic/claude-sonnet-4.6'],
    'normal': ['openrouter/deepseek/deepseek-v4.1-flash', 'openrouter/z-ai/glm-5.3', 'openrouter/z-ai/glm-5.2',
               'openrouter/anthropic/claude-sonnet-4.6'],
    'low': ['openrouter/~deepseek/deepseek-v4-flash-latest', 'openrouter/deepseek/deepseek-v4-flash',
            'openrouter/anthropic/claude-haiku-4.5'],
}
TIER_ROUTE = 'tier-{}'
def tier_models(tiers):
    """Every model a tier setup names, in first-seen order."""
    return list(dict.fromkeys(m for chain in tiers.values() for m in (chain if isinstance(chain, list) else [chain])))


def general_team_setup(*, owner, hub, models, tiers, management_hub, budget_ref):
    """The original General Team, with platform-budget models over Model Services."""
    # Older setups recorded one model per tier: they keep composing exact model refs.
    if all(isinstance(chain, list) for chain in tiers.values()):
        refs = {tier: 'fleet-route://' + TIER_ROUTE.format(tier) for tier in tiers}
        # The tier routes, and every other route the owner makes in Model
        # Services over these same services ('*'), follow the owner's edits.
        routes = {TIER_ROUTE.format(tier): 'current' for tier in tiers} | {'*': 'current'}
    else:
        refs = {tier: 'fleet-model://platform/' + quote(model, safe='') for tier, model in tiers.items()}
        routes = {}
    return {
        'protocol': 1, 'preset': 'general-team',
        'agent': {'protocol': 1, 'namespace': 'general-team',
                  'projects': [{'id': 'default', 'name': 'Default workspace', 'path': WORKSPACE}],
                  'active_project': 'default', 'default_project': 'default',
                  'settings': {'default_template_auto_update': False},
                  'models': {'fleet_tiers': refs}},
        'models': {'deployments': {'platform': {'$model': 'connector'}}, 'routes': routes, 'allow_wake': False},
        'model_apps': {'connector': {
            'deployment_id': 'platform', 'name': 'Platform models',
            'models': [{'id': model, 'context_limit': limit} for model, limit in models.items()],
            'app': {'scope': 'model-platform', 'bindings': {}, 'components': {'backend': {'values': {
                'connector': {'engine': 'api', 'endpoint': hub.rstrip('/') + '/litellm/v1',
                              'secret_ref': budget_ref}}}}}}},
        'files': {'sampling': {'state': 'unconfigured'}, 'image_generation': {'state': 'unconfigured'}},
        'desktop': {'user_seed': owner, 'catalog': [{'path': '/workspace/.pantheon/apps', 'scope': 'user'}],
                    'store': {'origin': hub.rstrip('/')}, 'data': {'mode': 'gateway'}},
        'evolution': {'execution': 'node', 'options': {
            'num_workers': 1, 'llm_weight': 0, 'function_weight': 1, 'evaluation_timeout': 600,
            'mutation_timeout': 900, 'max_tool_calls_per_mutation': 32, 'max_mutation_turns': 32}},
        'management': {'hub': management_hub},
    }
