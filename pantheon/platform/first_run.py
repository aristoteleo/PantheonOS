"""First-run Agent setup for a remote (cloud) platform deployment.

The owner starts it from the desktop. Their browser, logged in to Hub, mints a
revocable platform key and reads their model budget; this platform never sees
their login. With those two values the platform:

1. obtains the pinned App release set (release_source),
2. stages its packages on the owner's workspace node (release_set),
3. delivers the platform key and budget key to that node's vault only,
4. composes the original General Team with the ordinary profile compiler, and
   renders it for remote nodes instead of a bundled local Fleet.

The resulting recipe is returned for the owner to review and save through the
existing revision-checked Hub startup API; nothing is started here. No feature
is removed from the General Team to make it fit.
"""
import os
from urllib.parse import quote, urlsplit

from pantheon.apps.dependency_assembly import AssemblyError, _copy
from pantheon.apps.local_agent import _entries, compose_profile
from pantheon.apps.release_set import stage_release_set
from pantheon.platform.app_preset import startup_recipe

PLATFORM = 'linux-amd64'
# Pinned default release set (staging). PANTHEON_AGENT_RELEASE_URL/SHA256 override it.
RELEASE_URL = ('https://github.com/aristoteleo/PantheonOS/releases/download/'
               'agent-app-v0.7.0-staging.1/agent-release-set-linux-amd64.tar.gz')
RELEASE_SHA256 = '6f0fb9f006d4430bc7e9fc9603b383ab0dc716c150e8c990701921dd78b5f31e'
WORKSPACE = '/workspace/default_workspace'
BUDGET_REF = 'node-secret://platform-budget'
OWNER_REF_PREFIX = 'platform-owner'
DEFAULT_TIERS = {'high': 'openrouter/openai/gpt-5.5', 'normal': 'openrouter/openai/gpt-5.5',
                 'low': 'openrouter/openai/gpt-5.4-mini'}
# The profile compiler leaves these for a bundled local Fleet. A remote platform
# reaches a public-CA controller through Hub instead: these keys are omitted.
LOCAL_ONLY = {'controller', 'trust_roots_pem', 'directory_root'}


def bus_url(controller):
    host = urlsplit(controller).hostname
    if not host:
        raise AssemblyError('Fleet controller URL is required for the App bus')
    return os.environ.get('PANTHEON_FLEET_BUS_URL') or f'wss://{host}/nats'


def general_team_setup(*, owner, hub, models, tiers, management_hub):
    """The original General Team, with platform-budget models over Model Services."""
    refs = {tier: 'fleet-model://platform/' + quote(model, safe='') for tier, model in tiers.items()}
    return {
        'protocol': 1, 'preset': 'general-team',
        'agent': {'protocol': 1, 'namespace': 'general-team',
                  'projects': [{'id': 'default', 'name': 'Default workspace', 'path': WORKSPACE}],
                  'active_project': 'default', 'default_project': 'default',
                  'settings': {'default_template_auto_update': False},
                  'models': {'fleet_tiers': refs}},
        'models': {'deployments': {'platform': {'$model': 'connector'}}, 'routes': {}, 'allow_wake': False},
        'model_apps': {'connector': {
            'deployment_id': 'platform', 'name': 'Platform models',
            'models': [{'id': model, 'context_limit': limit} for model, limit in models.items()],
            'app': {'scope': 'model-platform', 'bindings': {}, 'components': {'backend': {'values': {
                'connector': {'engine': 'api', 'endpoint': hub.rstrip('/') + '/litellm/v1',
                              'secret_ref': BUDGET_REF}}}}}}},
        'files': {'sampling': {'state': 'unconfigured'}, 'image_generation': {'state': 'unconfigured'}},
        'desktop': {'user_seed': owner, 'catalog': [{'path': '/workspace/.pantheon/apps', 'scope': 'user'}],
                    'store': {'origin': hub.rstrip('/')}, 'data': {'mode': 'loopback'}},
        'evolution': {'execution': 'node', 'options': {
            'num_workers': 1, 'llm_weight': 0, 'function_weight': 1, 'evaluation_timeout': 600,
            'mutation_timeout': 900, 'max_tool_calls_per_mutation': 32, 'max_mutation_turns': 32}},
        'management': {'hub': management_hub},
    }


def render_remote(value, context, *, key=None):
    """Replace local-profile markers for remote nodes (see module docstring)."""
    if isinstance(value, dict):
        if '$local' in value:
            name = value['$local']
            if set(value) != {'$local'}:
                raise AssemblyError('Use an exact declared profile value')
            if name in LOCAL_ONLY:
                return _OMIT
            if name == 'owner_credential':
                if key not in context['owner_credentials']:
                    raise AssemblyError(f'No owner credential for {key!r} on the remote node')
                return _copy(context['owner_credentials'][key])
            if name not in context:
                raise AssemblyError(f'Unknown profile value {name!r}')
            return _copy(context[name])
        if value == {'auth': 'creds-base64'}:
            # App bus over TLS with a renewing owner credential (owned_bus fleet-key).
            return {'auth': 'fleet-key', 'url': context['bus_url']}
        result = {}
        for k, item in value.items():
            rendered = render_remote(item, context, key=k)
            if rendered is not _OMIT:
                result[k] = rendered
        return result
    if isinstance(value, list):
        return [render_remote(item, context) for item in value]
    return value


_OMIT = object()


def remote_recipe(spec, *, owner, node_id, owner_credentials, controller, operation_id):
    base = dict(owner_credentials=owner_credentials, workspace=WORKSPACE, bus_url=bus_url(controller),
                # Bus credentials are minted from the controller-bound owner key.
                fleet_credential=owner_credentials['controller'])

    def app(name, value):
        context = {**base, 'fleet_event_prefix': f'fleet.{owner}.apps.{name}'}
        return dict(node_id=node_id, revision=spec['packages'][value['package']]['revision'], generation=0,
                    scope=value['scope'], components=render_remote(value['components'], context),
                    bindings=render_remote(value['bindings'], context))
    recipe = dict(owner=owner, operation_id=operation_id,
                  apps={name: app(name, v) for name, v in spec['apps'].items()})
    if spec['model_apps']:
        recipe.update(kind='model-services', model_apps={
            name: {**v, 'app': app(name, v['app'])} for name, v in spec['model_apps'].items()})
    return startup_recipe(recipe)


async def workspace_node(resolver):
    """The owner's online workspace sandbox that runs Apps (exactly one)."""
    from pantheon.apps.builtin.fleet.inventory import node_inventory
    await resolver._ensure_client()
    nodes = [n for n in node_inventory(await resolver._list_nodes(max_age=0))['nodes']
             if n.get('kind') == 'sandbox' and 'fs:workspace' in (n.get('caps') or [])
             and n.get('status') in ('online', 'busy')
             and n.get('runtimes', {}).get('app-lifecycle') == '1'
             and f"{n.get('os')}-{n.get('arch')}" == PLATFORM]
    if len(nodes) != 1:
        raise AssemblyError('Start your workspace first: exactly one online workspace node is required')
    return nodes[0]['node_id']


async def prepare(*, resolver, owner, hub, controller, platform_key, budget, tiers=None, context_limit=200000,
                  cache, operation_id, release_url=None, release_sha256=None):
    """Stage, provision and compose. Returns {'node_id', 'recipe'}; starts nothing."""
    from pantheon.apps.lifecycle import FleetLifecycle
    from pantheon.models.credentials import RemoteModelCredentialVault
    from pantheon.models.platform_budget import deliver_platform_budget
    from pantheon.platform.owner_credentials import provision_owner_credentials
    from .release_source import release_set

    tiers = dict(tiers or DEFAULT_TIERS)
    if set(tiers) != {'low', 'normal', 'high'} or not all(isinstance(m, str) and m for m in tiers.values()):
        raise AssemblyError('Choose low, normal and high models')
    node_id = await workspace_node(resolver)
    lifecycle = FleetLifecycle(resolver)
    connector = {'engine': 'api', 'endpoint': hub.rstrip('/') + '/litellm/v1', 'secret_ref': BUDGET_REF}
    # Credentials first: a refused key or budget must not leave staged packages behind for nothing.
    vault = RemoteModelCredentialVault(lifecycle, owner=owner, node_id=node_id)
    await deliver_platform_budget(budget, vault=vault, ref=BUDGET_REF, expected_connector=connector)
    issued = await provision_owner_credentials(hub=hub, key=platform_key, owner=owner,
                                               node_ids=[node_id], ref_prefix=OWNER_REF_PREFIX)
    owner_credentials = issued['nodes'][node_id]

    root = await release_set(release_url or os.environ.get('PANTHEON_AGENT_RELEASE_URL') or RELEASE_URL,
                             release_sha256 or os.environ.get('PANTHEON_AGENT_RELEASE_SHA256') or RELEASE_SHA256,
                             cache)
    entries = _entries(root, PLATFORM)
    models = {model: context_limit for model in dict.fromkeys(tiers.values())}
    setup = general_team_setup(owner=owner, hub=hub, models=models, tiers=tiers,
                               management_hub=owner_credentials['hub'])
    spec = compose_profile(entries, setup)
    targets = dict(spec['apps'])
    targets.update({name: item['app'] for name, item in spec['model_apps'].items()})
    placements = {name: {'node_id': node_id, 'platform': PLATFORM, 'scope': app['scope'], 'generation': 0}
                  for name, app in targets.items()}
    await stage_release_set(lifecycle, root, owner=owner, placements=placements)
    recipe = remote_recipe(spec, owner=owner, node_id=node_id, owner_credentials=owner_credentials,
                           controller=controller, operation_id=operation_id)
    return {'node_id': node_id, 'recipe': recipe}
