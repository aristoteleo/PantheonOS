"""Complete local Agent product wiring from explicit owner choices.

This is a distribution preset, not a runtime or another tool implementation.
Both CLI and Desktop feed its output to the ordinary profile compiler. It never
reads ambient settings, chooses a model, issues credentials or starts an App.
"""
import json

from .agent_execution_client import execution_method_rules
from .dependency_assembly import AssemblyError, _copy, DEPLOYMENT_BYTES
from .tool_profiles import compile_tool_profile


PROVIDERS = {
    'files': ('file-manager', 'file_manager'),
    'shell': ('shell', 'shell'),
    'notebook': ('integrated-notebook', 'integrated_notebook'),
    'web': ('web', 'web'),
    'evolution': ('evolution', 'evolution'),
    'desktop': ('desktop', 'desktop'),
    'fleet': ('fleet', 'fleet'),
    'model-management': ('model-services-management', 'model_services'),
}


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise AssemblyError('Incomplete or conflicting General Team preset options')
    return value


def _files_policy(policy, references):
    """Only selected deployments/routes, never the whole Agent catalog."""
    from pantheon.models.client import parse_ref, parse_route_ref
    _object(policy, ('deployments', 'routes', 'allow_wake'))
    selected = {'deployments': {}, 'routes': {}, 'allow_wake': policy['allow_wake']}
    try:
        for ref in references:
            if ref.startswith('fleet-route://'):
                identity = parse_route_ref(ref)
                selected['routes'][identity] = policy['routes'][identity]
            else:
                identity, _ = parse_ref(ref)
                selected['deployments'][identity] = policy['deployments'][identity]
    except (AttributeError, TypeError, ValueError, KeyError):
        raise AssemblyError('Files models must have explicit Model Services access in this preset') from None
    return selected


def expand_general_team(entries, raw):
    value = _copy(raw, DEPLOYMENT_BYTES)
    _object(value, ('protocol', 'preset', 'agent', 'models', 'model_apps', 'files', 'desktop', 'evolution'),
            ('credentials', 'management', 'notebook'))
    if type(value['protocol']) is not int or value['protocol'] != 1 or value['preset'] != 'general-team':
        raise AssemblyError('Unknown local Agent preset')
    agent = _object(value['agent'], ('protocol', 'namespace', 'projects', 'models'),
                    ('settings', 'active_project', 'default_project'))
    if not isinstance(agent['models'], dict):
        raise AssemblyError('General Team needs explicit model configuration')
    if 'fleet_tiers' in agent['models']:
        tiers = agent['models']['fleet_tiers']
        if (not isinstance(tiers, dict) or set(tiers) != {'low', 'normal', 'high'}
                or not all(isinstance(ref, str) and ref for ref in tiers.values())):
            raise AssemblyError('Bind low, normal and high explicitly in the complete preset')
    # Explicit detailed bindings use the existing setup format. Never silently
    # discard custom MCP, tool, GUI or auxiliary bindings to apply this preset.
    if not isinstance(agent['projects'], list) or not agent['projects']:
        raise AssemblyError('General Team needs its explicit projects')
    project_ids = []
    for project in agent['projects']:
        _object(project, ('id', 'name', 'path'))
        if not isinstance(project['id'], str) or not project['id'] or project['id'] in project_ids:
            raise AssemblyError('General Team projects need distinct identities')
        project_ids.append(project['id'])
    contracts, files_policy = {}, None
    for alias, (app_id, toolset) in PROVIDERS.items():
        try:
            entry, root = entries[alias]
            source = json.loads((root/'app.json').read_text())
            if entry['app_id'] != app_id or source['id'] != app_id:
                raise ValueError
            uses = [f"{i['name']}@{i.get('version', 1)}" for i in source['provides']['interfaces']]
        except (KeyError, TypeError, ValueError, OSError):
            raise AssemblyError('General Team requires every declared provider in the selected product release') from None
        contract = {'app': alias, 'uses': uses}
        if alias == 'shell':
            contract['resource'] = {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}
        if alias == 'files':
            contract['service_methods'] = ['stat_path']
        _, policy, _ = compile_tool_profile(source, alias=alias, uses=uses,
            resource=contract.get('resource'), service_methods=contract.get('service_methods'))
        if alias == 'files':
            if not {'observe_images', 'generate_image'} <= policy['methods'].keys():
                raise AssemblyError('General Team requires the complete Files model capabilities')
            files_policy = policy
        contracts[toolset] = contract
    if entries.get('files-models', ({},))[0].get('app_id') != 'model-services-control':
        raise AssemblyError('General Team requires an independent Files model-access App')

    trust, workspace = {'$local': 'trust_roots_pem'}, {'$local': 'workspace'}
    owner, bus = {'$local': 'owner_credential'}, {'$local': 'fleet_credential'}
    files = _object(value['files'], ('sampling', 'image_generation'), ('limits',))
    sampling = _object(files['sampling'], ('model', 'max_tokens', 'max_requests_per_call'))
    images = _object(files['image_generation'], ('model', 'aliases', 'timeout_seconds'))
    if not isinstance(images['aliases'], dict):
        raise AssemblyError('Image aliases must explicitly select Model Services references')
    selection = _files_policy(value['models'], [sampling['model'], images['model'], *images['aliases'].values()])
    desktop = _object(value['desktop'], ('user_seed', 'catalog', 'store', 'data'), ('data_roots', 'credentials'))
    desktop_credentials = desktop.pop('credentials', {})
    if not isinstance(desktop_credentials, dict) or desktop_credentials.keys() - {'store'}:
        raise AssemblyError('Desktop optional credentials belong to its Store only')
    evolution = _object(value['evolution'], ('execution', 'options'), ('placement', 'credentials'))
    evolution_credentials = evolution.pop('credentials', {})
    if not isinstance(evolution_credentials, dict) or evolution_credentials.keys() - {'modal'}:
        raise AssemblyError('Evolution optional credentials belong to its explicit placement only')
    management = _object(value.get('management', {}), (), ('hub', 'hub_ca_pem'))
    if 'hub_ca_pem' in management and 'hub' not in management:
        raise AssemblyError('Cloud management trust requires an explicit Hub credential')
    notebook = _object(value.get('notebook', {}), (), ('execution_timeout', 'execution_logging'))

    def provider(scope, values, bindings=None, credentials=None):
        component = {'values': values}
        if credentials:
            component['credentials'] = credentials
        return {'scope': scope, 'components': {'backend': component}, 'bindings': bindings or {}}

    file_binding = {'credential': 'files', 'profile': 'file_manager'}
    agent.update(dependencies={'allocator': 'allocator', 'profiles': {'toolsets': {}, 'mcp_servers': {}},
        'defaults': {'toolsets': [], 'mcp_servers': [], 'primary_toolsets': ['fleet', 'model_services']}},
        auxiliary={'toolsets': {'file_manager': file_binding}},
        view_dependencies={identity: {'toolsets': {'file_manager': file_binding}} for identity in project_ids})
    providers = {
        'shell': {'scope': 'shared-shell', 'components': {}, 'bindings': {}},
        'web': {'scope': 'shared-web', 'components': {}, 'bindings': {}},
        'files-models': provider('files-models', {'model_services': {
            'protocol': 1, 'http_origin': {'$local': 'controller'}, 'trust_roots_pem': trust,
            'directory_root': {'$local': 'directory_root'},
            'policies': {'files': {'consumer': {'$app': 'files'}, **selection}}}}, credentials={'hub': owner}),
        'files': provider('shared-files', {
            'files': {'workspace': workspace, **({'limits': files['limits']} if 'limits' in files else {})},
            'sampling': {**sampling, 'credential': 'models', 'trust_roots_pem': trust},
            'image_generation': {**images, 'credential': 'models', 'trust_roots_pem': trust}}, bindings={
                'models': {'app_id': 'model-services-control', 'component': 'backend',
                    'provider': {'$app': 'files-models', 'component': 'backend', 'port': 'http'},
                    'methods': {'model_services_control': {'arguments': ['operation', 'arguments'],
                                                          'bound': {'policy_id': 'files'}}}}}),
        'notebook': provider('shared-notebook', {'notebook': {'workspace': workspace, **notebook}}),
        'evolution': provider('evolution-controller', {'evolution': {
            **evolution, 'agent_credential': 'agent', 'agent_ca_pem': trust}}, bindings={
                'agent': {'app_id': 'agent', 'component': 'backend',
                    'provider': {'$app': 'agent', 'component': 'backend', 'port': 'http'},
                    'methods': execution_method_rules('evolution-controller')}}, credentials=evolution_credentials),
        'desktop': provider('shared-desktop', {'desktop': {
            'data_roots': [workspace], **desktop, 'fleet': {'auth': 'creds-base64'},
            'events': {'auth': 'creds-base64'}, 'event_prefix': {'$local': 'fleet_event_prefix'}}},
            credentials={**desktop_credentials, 'fleet': bus, 'events': bus}),
        'fleet': provider('shared-fleet-management', {'fleet': {
            'bus': {'auth': 'creds-base64'}, 'controller_ca_pem': trust}},
            credentials={'fleet': bus, 'controller': owner}),
        'model-management': provider('shared-model-management', {'model_management': {
            'directory_root': {'$local': 'directory_root'}, 'bus': {'auth': 'creds-base64'},
            'controller_ca_pem': trust, **({'hub_ca_pem': management['hub_ca_pem']} if 'hub_ca_pem' in management else {})}},
            credentials={'fleet': bus, 'controller': owner, **({'hub': management['hub']} if 'hub' in management else {})}),
    }
    return {'protocol': 1, 'agent': agent, 'models': value['models'], 'model_apps': value['model_apps'],
        'credentials': value.get('credentials', {}), 'providers': providers, 'tools': {},
        'tool_contracts': contracts, 'extra_bindings': {'files': {**files_policy, 'component': 'backend'}}}
