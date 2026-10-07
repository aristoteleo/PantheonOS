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
# The budget reference is stable per owner: it holds the same LiteLLM virtual
# key every run (delivery is idempotent), and the model connector refuses a
# configuration that differs from the one it retains. Owner-key references are
# per run: a retry mints a new platform key, and a vault never replaces an
# existing credential under the same reference.
BUDGET_REF = 'node-secret://platform-budget'


def _refs(operation_id):
    run = operation_id.rsplit('-', 1)[-1][-12:]
    return BUDGET_REF, f'platform-owner-{run}'


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


def general_team_setup(*, owner, hub, models, tiers, management_hub, budget_ref):
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
                              'secret_ref': budget_ref}}}}}}},
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


def existing_generations(state, spec):
    """Continue the generation of a stopped instance with the same identity.

    A Fleet instance is identified by (release digest, scope): an unchanged App
    from an earlier setup (e.g. the model connector) already has generation N
    there, and a start must present it. A running one must be stopped first.
    """
    instances = list((state.get('instances') or {}).values())
    targets = dict(spec['apps'])
    targets.update({name: item['app'] for name, item in spec['model_apps'].items()})
    generations = {}
    for name, app in targets.items():
        digest = spec['packages'][app['package']]['revision']
        same = [i for i in instances if i.get('digest') == digest and i.get('scope') == app['scope']]
        if not same:
            continue
        if any(i.get('state') != 'stopped' for i in same):
            raise AssemblyError(f'{name} is already running on your workspace; stop it in Fleet before setting up again')
        generations[name] = max(int(i.get('generation') or 0) for i in same)
    return generations


async def retire_stale_registrations(directory, state, deployment_ids, node_id):
    """Forget directory rows left by an earlier setup whose connector has stopped.

    A started connector registers itself again at its new generation, and
    registration refuses to replace a row that differs. A row is only removed
    when its bound instance on this node is stopped (or gone); a live one, or a
    row for another node or a group, stays for explicit Model Services management.
    """
    rows = {row['deployment_id']: row for row in await directory.deployments()}
    for deployment_id in deployment_ids:
        row = rows.get(deployment_id)
        if row is None:
            continue
        if row.get('mode') == 'group' or row.get('node_id') != node_id:
            raise AssemblyError(f'Model service {deployment_id!r} is registered elsewhere; remove it in Model Services first')
        if row['state'] not in ('stopped', 'draft'):
            bound = (state.get('instances') or {}).get((row.get('binding') or {}).get('instance_id'))
            if bound and (bound.get('state') != 'stopped' or bound.get('resources') or bound.get('reservations')):
                raise AssemblyError(f'Model service {deployment_id!r} is still running; stop it in Model Services first')
            row = await directory.save({**row, 'state': 'stopped'})
        await directory.remove(deployment_id, row['revision'])


def remote_recipe(spec, *, owner, node_id, owner_credentials, controller, operation_id, generations=None):
    base = dict(owner_credentials=owner_credentials, workspace=WORKSPACE, bus_url=bus_url(controller),
                # Bus credentials are minted from the controller-bound owner key.
                fleet_credential=owner_credentials['controller'])

    def app(name, value):
        context = {**base, 'fleet_event_prefix': f'fleet.{owner}.apps.{name}'}
        return dict(node_id=node_id, revision=spec['packages'][value['package']]['revision'],
                    generation=(generations or {}).get(name, 0),
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
                  cache, operation_id, release_url=None, release_sha256=None, directory=None):
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
    budget_ref, owner_prefix = _refs(operation_id)
    connector = {'engine': 'api', 'endpoint': hub.rstrip('/') + '/litellm/v1', 'secret_ref': budget_ref}
    # Credentials first: a refused key or budget must not leave staged packages behind for nothing.
    vault = RemoteModelCredentialVault(lifecycle, owner=owner, node_id=node_id)
    await deliver_platform_budget(budget, vault=vault, ref=budget_ref, expected_connector=connector)
    issued = await provision_owner_credentials(hub=hub, key=platform_key, owner=owner,
                                               node_ids=[node_id], ref_prefix=owner_prefix)
    owner_credentials = issued['nodes'][node_id]

    root = await release_set(release_url or os.environ.get('PANTHEON_AGENT_RELEASE_URL') or RELEASE_URL,
                             release_sha256 or os.environ.get('PANTHEON_AGENT_RELEASE_SHA256') or RELEASE_SHA256,
                             cache)
    entries = _entries(root, PLATFORM)
    models = {model: context_limit for model in dict.fromkeys(tiers.values())}
    setup = general_team_setup(owner=owner, hub=hub, models=models, tiers=tiers,
                               management_hub=owner_credentials['hub'], budget_ref=budget_ref)
    spec = compose_profile(entries, setup)
    targets = dict(spec['apps'])
    targets.update({name: item['app'] for name, item in spec['model_apps'].items()})
    state = await lifecycle.status(node_id)
    generations = existing_generations(state, spec)
    if directory is not None:
        await retire_stale_registrations(
            directory, state, [item['deployment_id'] for item in setup['model_apps'].values()], node_id)
    placements = {name: {'node_id': node_id, 'platform': PLATFORM, 'scope': app['scope'],
                         'generation': generations.get(name, 0)} for name, app in targets.items()}
    await stage_release_set(lifecycle, root, owner=owner, placements=placements)
    recipe = remote_recipe(spec, owner=owner, node_id=node_id, owner_credentials=owner_credentials,
                           controller=controller, operation_id=operation_id, generations=generations)
    return {'node_id': node_id, 'recipe': recipe}
