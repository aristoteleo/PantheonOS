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
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from pantheon.apps.dependency_assembly import AssemblyError, _copy
from pantheon.apps.local_agent import _entries, compose_profile
from pantheon.apps.release_set import stage_release_set
from pantheon.platform.app_preset import startup_recipe
from pantheon.utils.log import logger

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
# Bumped when a fresh setup composes the Agent differently (2: tier routes with
# failover and owner routes). An older setup is offered an in-place update.
SETUP_VERSION = 2


def tier_models(tiers):
    """Every model a tier setup names, in first-seen order."""
    return list(dict.fromkeys(m for chain in tiers.values() for m in (chain if isinstance(chain, list) else [chain])))
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


def existing_generations(states, spec, nodes):
    """Continue the generation of a stopped instance with the same identity.

    A Fleet instance is identified by (release digest, scope): an unchanged App
    from an earlier setup (e.g. the model connector) already has generation N
    there, and a start must present it. A running one must be stopped first.
    """
    targets = dict(spec['apps'])
    targets.update({name: item['app'] for name, item in spec['model_apps'].items()})
    generations = {}
    for name, app in targets.items():
        digest = spec['packages'][app['package']]['revision']
        instances = (states[nodes[name]].get('instances') or {}).values()
        same = [i for i in instances if i.get('digest') == digest and i.get('scope') == app['scope']]
        if not same:
            continue
        if any(i.get('state') != 'stopped' for i in same):
            raise AssemblyError(f'{name} is already running; stop it in Fleet before setting up again')
        generations[name] = max(int(i.get('generation') or 0) for i in same)
    return generations


async def retire_stale_registrations(directory, states, deployment_ids, node_id):
    """Stop directory rows left by an earlier setup whose connector has stopped.

    A started connector registers itself again at its new generation and takes
    over a stopped row in place (see prepared_registration). A row is only stopped
    when its bound instance is stopped (or gone) on its own node, including a
    connector that moved to another node; a live one, a group, or a row on a
    node not reported in states stays for explicit Model Services management.
    """
    rows = {row['deployment_id']: row for row in await directory.deployments()}
    for deployment_id in deployment_ids:
        row = rows.get(deployment_id)
        if row is None:
            continue
        if row.get('mode') == 'group' or row.get('node_id') not in states:
            raise AssemblyError(f'Model service {deployment_id!r} is registered elsewhere; remove it in Model Services first')
        if row['state'] not in ('stopped', 'draft'):
            state = states[row['node_id']]
            bound = (state.get('instances') or {}).get((row.get('binding') or {}).get('instance_id'))
            if bound and (bound.get('state') != 'stopped' or bound.get('resources') or bound.get('reservations')):
                raise AssemblyError(f'Model service {deployment_id!r} is still running; stop it in Model Services first')
            await directory.save({**row, 'state': 'stopped'})
        # The stopped row stays: the new Connector's registration replaces it in
        # place, so the owner's routes over this service remain valid.


def remote_recipe(spec, *, owner, nodes, credentials, controller, operation_id, generations=None):
    """nodes: {app name: node_id}; credentials: {node_id: owner credentials there}."""
    def app(name, value):
        node_id, owner_credentials = nodes[name], credentials[nodes[name]]
        context = dict(owner_credentials=owner_credentials, workspace=WORKSPACE, bus_url=bus_url(controller),
                       # Bus credentials are minted from the controller-bound owner key.
                       fleet_credential=owner_credentials['controller'],
                       fleet_event_prefix=f'fleet.{owner}.apps.{name}')
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


# A frontend surface, not something a Fleet node offers.
FRONTEND_ONLY = {'dom'}


def local_node():
    """The node of the Runner beside this platform (the brain), if any."""
    path = Path(os.environ.get('PANTHEON_FLEET_STATE_DIR') or '/tmp/fleet-node') / 'runtime.json'
    try:
        return json.loads(path.read_text()).get('node_id') or None
    except (OSError, ValueError):
        return None


async def app_nodes(resolver):
    """Online nodes of this owner that can run release-set Apps."""
    from pantheon.apps.builtin.fleet.inventory import node_inventory
    await resolver._ensure_client()
    return [n for n in node_inventory(await resolver._list_nodes(max_age=0))['nodes']
            if n.get('status') in ('online', 'busy')
            and n.get('runtimes', {}).get('app-lifecycle') == '1'
            and f"{n.get('os')}-{n.get('arch')}" == PLATFORM]


def place(manifest, nodes, local=None):
    """Choose a node by placement.requires x caps (the topology configuration).

    An App that needs nothing beyond what any node offers runs beside the
    platform (the brain: fast to start, independent of the workspace); one
    that needs the workspace, a display or network goes where those are.
    `prefer` breaks ties by node kind, then the local node wins.
    """
    placement = manifest.get('placement') or {}
    requires = set(placement.get('requires') or []) - FRONTEND_ONLY
    prefer = list(placement.get('prefer') or [])
    fits = [n for n in nodes if requires <= set(n.get('caps') or [])]
    if not fits:
        raise AssemblyError(f"No online node offers {sorted(requires)} for {manifest.get('id')}; "
                            'start your workspace first')
    return min(fits, key=lambda n: (n.get('kind') not in prefer, n['node_id'] != local, n['node_id']))['node_id']


def compose_release(entries, setup, *, operation_id, generations=None):
    """The preset recipe for one release set and the inputs recorded at setup.

    setup: owner, hub, controller, tiers, models, budget_ref, nodes ({App:
    node}) and credentials ({node: owner credentials}). The same inputs with
    another release yield an upgrade candidate; see preset_release.
    """
    management = setup['nodes'].get('model-management') or sorted(setup['credentials'])[0]
    team = general_team_setup(owner=setup['owner'], hub=setup['hub'], models=setup['models'], tiers=setup['tiers'],
                              management_hub=setup['credentials'][management]['hub'], budget_ref=setup['budget_ref'])
    spec = compose_profile(entries, team)
    return spec, remote_recipe(spec, owner=setup['owner'], nodes=setup['nodes'], credentials=setup['credentials'],
                               controller=setup['controller'], operation_id=operation_id, generations=generations)


async def carry_data(lifecycle, spec, nodes, *, operation_id, timeout=1200):
    """Copy each App's previous release data into the new package of a fresh setup.

    Data belongs to a (release digest, scope) instance, so a fresh setup on a
    new release would otherwise start every App (e.g. the Agent's chats) empty.
    Like a release update, the newest stopped instance in the same scope is
    copied with Fleet's clone_data into generation 0 of the new package; the
    source and its data are kept. A running source or a failed copy only means
    that App starts empty: setup is never blocked. Returns {App: source}.
    """
    targets = dict(spec['apps'])
    targets.update({name: item['app'] for name, item in spec['model_apps'].items()})
    pending, carried = [], {}
    for name, app in targets.items():
        node, digest = nodes[name], spec['packages'][app['package']]['revision']
        state = await lifecycle.status(node)
        instances = list((state.get('instances') or {}).values())
        if any(i.get('digest') == digest and i.get('scope') == app['scope'] for i in instances):
            continue  # this release already has its data here (existing_generations)
        prior = [i for i in instances if i.get('scope') == app['scope'] and i.get('digest') != digest
                 and int(i.get('generation') or 0) > 0]
        if not prior:
            continue
        source = max(prior, key=lambda i: int(i['generation']))
        if source.get('state') != 'stopped' or source.get('resources') or source.get('reservations'):
            logger.warning(f'[first-run] {name}: previous data is still in use; starting empty')
            continue
        origin = {'digest': source['digest'], 'generation': int(source['generation'])}
        try:
            install = f'{operation_id}-carry-install-{name}'[:80]
            await lifecycle.submit(node, 'install', digest, scope=app['scope'], generation=0, operation_id=install)
            pending.append((name, node, install, 'install', digest, app['scope'], origin))
        except Exception as exc:
            logger.warning(f'[first-run] {name}: install for data copy failed: {exc}')
    deadline = asyncio.get_running_loop().time() + timeout
    while pending:
        await asyncio.sleep(2)
        still = []
        for name, node, op_id, step, digest, scope, origin in pending:
            operation = ((await lifecycle.status(node)).get('operations') or {}).get(op_id) or {}
            if operation.get('state') in ('queued', 'running', None) and asyncio.get_running_loop().time() < deadline:
                still.append((name, node, op_id, step, digest, scope, origin))
            elif operation.get('state') != 'succeeded':
                logger.warning(f'[first-run] {name}: {step} for data copy did not succeed; starting empty')
            elif step == 'install':
                copy = f'{operation_id}-carry-copy-{name}'[:80]
                try:
                    await lifecycle.submit(node, 'clone_data', digest, scope=scope, generation=0,
                                           operation_id=copy, data_source=origin)
                    still.append((name, node, copy, 'copy', digest, scope, origin))
                except Exception as exc:
                    logger.warning(f'[first-run] {name}: data copy failed: {exc}')
            else:
                carried[name] = origin
        pending = still
    return carried


async def prepare(*, resolver, owner, hub, controller, platform_key, budget, tiers=None, context_limit=200000,
                  cache, operation_id, release_url=None, release_sha256=None, directory=None):
    """Place, stage, provision and compose. Returns {'nodes', 'recipe', 'setup'}; starts nothing."""
    from pantheon.apps.lifecycle import FleetLifecycle
    from pantheon.models.credentials import RemoteModelCredentialVault
    from pantheon.models.platform_budget import deliver_platform_budget
    from pantheon.platform.owner_credentials import provision_owner_credentials
    from .release_source import release_set

    tiers = {tier: list(chain) for tier, chain in (tiers or DEFAULT_TIERS).items()}
    if (set(tiers) != {'low', 'normal', 'high'}
            or not all(1 <= len(chain) <= 16 and all(isinstance(m, str) and m for m in chain) for chain in tiers.values())):
        raise AssemblyError('Choose low, normal and high model chains')
    release = {'url': release_url or os.environ.get('PANTHEON_AGENT_RELEASE_URL') or RELEASE_URL,
               'sha256': release_sha256 or os.environ.get('PANTHEON_AGENT_RELEASE_SHA256') or RELEASE_SHA256}
    root = await release_set(release['url'], release['sha256'], cache)
    entries = _entries(root, PLATFORM)
    models = {model: context_limit for model in tier_models(tiers)}
    budget_ref, owner_prefix = _refs(operation_id)

    def compose(management_hub):
        setup = general_team_setup(owner=owner, hub=hub, models=models, tiers=tiers,
                                   management_hub=management_hub, budget_ref=budget_ref)
        return setup, compose_profile(entries, setup)

    # Placement depends only on which package each App runs, not on credentials.
    setup, spec = compose({'endpoint': hub.rstrip('/'), 'key': 'pbk_' + '0' * 43})
    targets = dict(spec['apps'])
    targets.update({name: item['app'] for name, item in spec['model_apps'].items()})
    candidates, local = await app_nodes(resolver), local_node()
    nodes = {name: place(json.loads((Path(entries[app['package']][1]) / 'app.json').read_text()), candidates, local)
             for name, app in targets.items()}
    used = sorted(set(nodes.values()))

    lifecycle = FleetLifecycle(resolver)
    connector_node = nodes[next(iter(spec['model_apps']))]
    connector = {'engine': 'api', 'endpoint': hub.rstrip('/') + '/litellm/v1', 'secret_ref': budget_ref}
    # Credentials before staging: a refused key or budget leaves no packages behind for nothing.
    vault = RemoteModelCredentialVault(lifecycle, owner=owner, node_id=connector_node)
    await deliver_platform_budget(budget, vault=vault, ref=budget_ref, expected_connector=connector)
    issued = await provision_owner_credentials(hub=hub, key=platform_key, owner=owner,
                                               node_ids=used, ref_prefix=owner_prefix)
    credentials = {node: issued['nodes'][node] for node in used}
    inputs = dict(owner=owner, hub=hub, controller=controller, tiers=tiers, models=models,
                  budget_ref=budget_ref, nodes=nodes, credentials=credentials)
    spec, _ = compose_release(entries, inputs, operation_id=operation_id)

    states = {node: await lifecycle.status(node) for node in {*used, *(n['node_id'] for n in candidates)}}
    generations = existing_generations(states, spec, nodes)
    if directory is not None:
        await retire_stale_registrations(
            directory, states, [item['deployment_id'] for item in spec['model_apps'].values()], connector_node)
    placements = {name: {'node_id': nodes[name], 'platform': PLATFORM, 'scope': app['scope'],
                         'generation': generations.get(name, 0)} for name, app in targets.items()}
    await stage_release_set(lifecycle, root, owner=owner, placements=placements)
    carried = await carry_data(lifecycle, spec, nodes, operation_id=operation_id)
    if carried:
        logger.info(f'[first-run] carried previous data into: {sorted(carried)}')
    _, recipe = compose_release(entries, inputs, operation_id=operation_id, generations=generations)
    return {'nodes': nodes, 'recipe': recipe, 'setup': {**inputs, 'release': release, 'version': SETUP_VERSION}}
