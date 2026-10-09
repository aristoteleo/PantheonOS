"""Compile the General Team into a deployment profile shipped in the release set.

The Fleet controller deploys Apps from a desired-state deployment
(docs/fleet-orchestration.md). Composing the General Team (Agent, providers,
allocator and model policies) happens here, at release build time, against the
release's own packages; the result is ``profile.json`` beside
``release-set.json``. Setting up an owner's Agent fills a few named inputs and
saves the deployment; no Python runs on that path.

Profile placeholders:

- ``{"$input": name, "suffix": s}``: an environment value the Hub fills
  (``hub``, ``controller``, ``bus_url``), followed by ``suffix``.
- ``{"$secret": name}`` / ``{"$secret_ref": name}`` and ``{"$fleet": ...}``
  are left for the controller (secrets it delivers to the App's node, and the
  owner's fleet values). ``secrets`` says what each secret holds and where
  it is used.
- ``{"$app": name, ...}`` (in bindings and configuration) are exact instances
  the controller resolves when it starts the App.
"""
import argparse
import asyncio
import json
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.local_agent import _entries, compose_profile

PROTOCOL = 1
PROFILE = 'profile.json'
WORKSPACE = '/workspace/default_workspace'
# Values general_team_setup needs, replaced by placeholders after composing.
HUB = 'https://hub.profile.invalid'
OWNER = 'f_0000000000000000'
BUDGET = 'node-secret://profile-budget'
OWNER_HUB = {'ref': 'node-secret://profile-owner-hub', 'endpoint': HUB}
# Local-profile values with no meaning on remote nodes (render_remote omits them).
LOCAL_ONLY = {'controller', 'directory_root', 'trust_roots_pem'}
# What each secret holds; the Hub supplies the values at setup.
SECRETS = {
    'owner-hub': {'value': 'platform_key', 'endpoint': {'$input': 'hub'}},
    'owner-controller': {'value': 'platform_key', 'endpoint': {'$input': 'controller'}},
    'directory': {'value': 'platform_key', 'endpoint': {'$input': 'hub', 'suffix': '/api/model-services'}},
    'budget': {'value': 'budget_key', 'endpoint': {'$input': 'hub', 'suffix': '/litellm/v1'}},
}
_OMIT = object()


def _render(value, app, *, key=None):
    """Replace composition-time values with profile placeholders."""
    if isinstance(value, dict):
        if '$local' in value:
            name = value['$local']
            if name in LOCAL_ONLY:
                return _OMIT
            if name == 'owner_credential':
                if key not in ('hub', 'controller'):
                    raise AssemblyError(f'{app}: no owner credential {key!r} for remote nodes')
                return {'$secret': 'owner-' + key}
            if name == 'fleet_credential':
                return {'$secret': 'owner-controller'}
            if name == 'fleet_event_prefix':
                return {'$fleet': 'event_prefix'}
            if name == 'workspace':
                return WORKSPACE
            raise AssemblyError(f'{app}: unknown profile value {name!r}')
        if value == {'auth': 'creds-base64'}:
            # App bus over TLS with the controller-bound owner key.
            return {'auth': 'fleet-key', 'url': {'$input': 'bus_url'}}
        if value == OWNER_HUB:
            return {'$secret': 'owner-hub'}
        if set(value) == {'$model'}:
            return 'current'  # the Connector registers its running instance itself
        out = {}
        for k, item in value.items():
            rendered = _render(item, app, key=k)
            if rendered is not _OMIT:
                out[k] = rendered
        return out
    if isinstance(value, list):
        return [_render(item, app) for item in value]
    if value == OWNER:
        return {'$fleet': 'id'}
    if value == BUDGET:
        return {'$secret_ref': 'budget'}
    if isinstance(value, str) and value.startswith(HUB):
        return {'$input': 'hub', 'suffix': value[len(HUB):]}
    return value


def _app(name, app):
    components = _render(app['components'], name)
    bindings = {}
    for alias, binding in app['bindings'].items():
        provider = binding['provider']
        if set(provider) - {'$app', 'component', 'port'} or provider.get('component', 'backend') != 'backend':
            raise AssemblyError(f'{name}.{alias}: bindings reach another App backend')
        bindings[alias] = {'$app': provider['$app'], 'component': binding['component'],
                           'app_id': binding['app_id'], 'methods': binding['methods']}
    out = {'package': app['package'], 'scope': app['scope'], 'intent': 'running', 'placement': {}}
    if components:
        out['config'] = components
    if bindings:
        out['bindings'] = bindings
    return out


def compile_profile(spec, *, tiers, catalog=()):
    """The deployment profile for a composed General Team spec.

    tiers: {tier: [model, ...]} seeds the tier routes; catalog: extra
    [{id, suggested}] the platform Connector offers when the service has them.
    """
    apps = {name: _app(name, app) for name, app in spec['apps'].items()}
    for name, item in spec['model_apps'].items():
        app = _app(name, item['app'])
        values = app['config']['backend']['values']
        required = [{'id': m['id'], 'required': True, 'context_limit': m['context_limit']} for m in item['models']]
        listed = {m['id'] for m in required}
        values['directory'] = {
            'deployment_id': item['deployment_id'], 'name': item['name'],
            'models': required + [dict(m) for m in catalog if m['id'] not in listed],
            'routes': {'tier-' + tier: list(chain) for tier, chain in tiers.items()},
        }
        app['config']['backend']['credentials'] = {'directory': {'$secret': 'directory'}}
        apps[name] = app
    used = sorted({s for s in SECRETS if s in json.dumps(apps)})
    return {'protocol': PROTOCOL, 'name': 'general-team',
            'inputs': {'hub': 'Public Hub origin', 'controller': 'Fleet controller origin',
                       'bus_url': 'App bus WebSocket URL'},
            'secrets': {name: SECRETS[name] for name in used},
            'spec': {'apps': apps, 'secrets': used}}


async def platform_catalog():
    """The original picker's platform models, with labelled capabilities."""
    from pantheon.models.model_metadata import suggest
    from pantheon.utils import openrouter_catalog
    await openrouter_catalog.ensure_fresh()
    groups = openrouter_catalog.reorder_and_filter(openrouter_catalog.by_vendor())
    models = list(dict.fromkeys(m for group in groups.values() for m in group))
    suggestions = await suggest(models)
    return [{'id': m, 'suggested': {k: v for k, v in (suggestions.get(m) or {}).items() if v is not None}}
            for m in models]


def write_profile(root, platform, *, tiers, catalog=()):
    """Compose against the release's own packages and write profile.json."""
    from pantheon.platform.first_run import general_team_setup, tier_models
    root = Path(root)
    entries = _entries(root, platform)
    setup = general_team_setup(owner=OWNER, hub=HUB, models={m: 200000 for m in tier_models(tiers)},
                               tiers=tiers, management_hub=OWNER_HUB, budget_ref=BUDGET)
    profile = compile_profile(compose_profile(entries, setup), tiers=tiers, catalog=catalog)
    leftover = [marker for marker in (HUB, OWNER, BUDGET, '$local', '$model') if marker in json.dumps(profile['spec'])]
    if leftover:
        raise AssemblyError(f'profile still contains composition values: {leftover}')
    with (root / PROFILE).open('x') as stream:
        json.dump(profile, stream, indent=1, sort_keys=True)
        stream.write('\n')
    return profile


def main():
    from pantheon.platform.first_run import DEFAULT_TIERS
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--release-set', required=True, type=Path)
    parser.add_argument('--platform', default='linux-amd64')
    parser.add_argument('--no-catalog', action='store_true', help='Publish the tier models only')
    args = parser.parse_args()
    catalog = [] if args.no_catalog else asyncio.run(platform_catalog())
    profile = write_profile(args.release_set, args.platform, tiers=DEFAULT_TIERS, catalog=catalog)
    print(f"{len(profile['spec']['apps'])} Apps, {len(catalog)} catalog models")


if __name__ == '__main__':
    main()
