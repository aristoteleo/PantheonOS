"""Create a new local General Team setup from explicit first-run choices.

No App is started, no key is tested against a network service and no existing
profile is modified. Desktop and terminal clients share this owner operation.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from pantheon.apps.credentials import app_credential_endpoint
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.local_agent import read_bundle, compose_profile
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.models.client import model_ref
from pantheon.models.managed import module
from .app_preset import _unique_fields
from .local_credentials import read_credentials


def prepare(entries, choices, root):
    """Pure choice-to-preset conversion followed by the ordinary compiler."""
    required = {'protocol', 'project_name', 'engine', 'endpoint', 'tiers', 'store_origin'}
    if (not isinstance(choices, dict) or not required <= choices.keys()
            or choices.keys() - required - {'key', 'context_limit', 'image_model', 'image_inspection'}
            or type(choices['protocol']) is not int or choices['protocol'] != 1):
        raise AssemblyError('Supply the complete first-run choices')
    name = choices['project_name']
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise AssemblyError('Choose a project name of at most 120 characters')
    if choices['engine'] not in ('ollama', 'lmstudio', 'sglang', 'api'):
        raise AssemblyError('Choose a supported text model service')
    tiers = choices['tiers']
    if (not isinstance(tiers, dict) or set(tiers) != {'low', 'normal', 'high'}
            or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in tiers.values())):
        raise AssemblyError('Select low, normal and high model ids explicitly')
    limit = choices.get('context_limit')
    if limit is not None and (type(limit) is not int or not 512 <= limit <= 1 << 24):
        raise AssemblyError('An optional context limit must be between 512 and 16777216 tokens')
    key = choices.get('key', '')
    if not isinstance(key, str) or len(key) > 8192 or any(not 33 <= ord(c) <= 126 for c in key):
        raise AssemblyError('Supply a valid API key or leave it empty')
    inspection = choices.get('image_inspection', False)
    image = choices.get('image_model', '')
    if (type(inspection) is not bool or not isinstance(image, str) or len(image) > 200
            or image and (not image.strip() or choices['engine'] != 'api' or image in tiers.values())):
        raise AssemblyError('Choose a separate image model on an API service, or leave it empty')
    endpoint = app_credential_endpoint(choices['endpoint'])
    if not endpoint.startswith(('http://', 'https://')):
        raise AssemblyError('Models require an HTTP(S) endpoint')
    connector = module('server').validate_config({'engine': choices['engine'], 'endpoint': endpoint})
    connector.pop('credential_file')
    ref = 'node-secret://selected-model'
    secrets = {'protocol': 1, 'credentials': {}}
    if key:
        connector['secret_ref'] = ref
        secrets['credentials'][ref] = {'endpoint': connector['endpoint'], 'key': key}
    store = app_credential_endpoint(choices['store_origin'])
    if not store.startswith(('http://', 'https://')):
        raise AssemblyError('Store requires an HTTP(S) origin')
    models = [{'id': model, **({'context_limit': limit} if limit is not None else {})}
              for model in dict.fromkeys(tiers.values())]
    if image: models.append({'id': image, 'operations': ['image']})
    identity = 'agent-' + uuid.uuid4().hex
    setup = {'protocol': 1, 'preset': 'general-team', 'agent': {
        'protocol': 1, 'namespace': identity,
        'projects': [{'id': 'workspace', 'name': name, 'path': {'$local': 'workspace'}}],
        'active_project': 'workspace', 'default_project': 'workspace',
        'models': {'fleet_tiers': {tier: model_ref('local', model) for tier, model in tiers.items()}}},
        'models': {'deployments': {'local': {'$model': 'connector'}}, 'routes': {}, 'allow_wake': False},
        'model_apps': {'connector': {'deployment_id': 'local', 'name': 'Selected model service',
            'models': models, 'app': {'scope': 'model-local', 'components': {'backend': {
                'values': {'connector': connector}}}, 'bindings': {}}}},
        'files': {
            'sampling': {'model': model_ref('local', tiers['normal']), 'max_tokens': 1024, 'max_requests_per_call': 2}
                if inspection else {'state': 'unconfigured'},
            'image_generation': {'model': model_ref('local', image), 'aliases': {}, 'timeout_seconds': 120}
                if image else {'state': 'unconfigured'}},
        'desktop': {'user_seed': identity, 'catalog': [{'path': str(root/'catalog'), 'scope': 'user'}],
            'store': {'origin': store}, 'data': {'mode': 'loopback'}},
        'evolution': {'execution': 'node', 'options': {}}}
    return setup, secrets, compose_profile(entries, setup)


def create(bundle, root, workspace, launcher, choices):
    root, workspace, bundle = Path(root), Path(workspace), Path(bundle)
    if (not all(p.is_absolute() for p in (root, workspace, bundle)) or not workspace.is_dir()
            or root.exists() or root.is_symlink() or root.resolve().is_relative_to(workspace.resolve())
            or root.resolve().is_relative_to(bundle.resolve())
            or not isinstance(launcher, list) or not 1 <= len(launcher) <= 8
            or any(not isinstance(v, str) or not v or len(v) > 4096 or '\0' in v for v in launcher)
            or not Path(launcher[0]).is_absolute() or not os.access(launcher[0], os.X_OK)):
        raise AssemblyError('Choose an existing workspace, executable runtime and a new private setup directory outside the workspace')
    _, entries = read_bundle(bundle)
    setup, secrets, spec = prepare(entries, choices, root)
    # Reserve a new directory exclusively; never replace even an empty profile.
    root.mkdir(mode=0o700)
    journal = OwnerJournal(root)
    journal._private(root, directory=True)
    (root/'catalog').mkdir(mode=0o700)
    journal._write(root/'setup.json', setup)
    launch = {'protocol': 1, 'launcher': launcher, 'bundle': str(bundle),
        'setup': str(root/'setup.json'), 'profile': str(root/'profile'), 'workspace': str(workspace)}
    if secrets['credentials']:
        journal._write(root/'credentials.json', secrets)
        read_credentials(root/'credentials.json', spec, workspace)
        launch['credentials'] = str(root/'credentials.json')
    # Launch metadata is the completion receipt and is published last. A partial
    # directory after I/O failure is never considered a usable launch choice.
    journal._write(root/'launch.json', launch)
    return launch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'output', 'workspace', 'python'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768: raise ValueError('Input too large')
        choices = json.loads(raw, object_pairs_hook=_unique_fields)
        result = create(args.bundle, args.output, args.workspace,
                        [args.python, '-m', 'pantheon'], choices)
        print(json.dumps(result), flush=True)
    except Exception:
        print('Could not create the setup. Check the product, private destination and model choices. No Apps were started.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__': main()
