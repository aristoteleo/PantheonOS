"""Complete packaged tool faces, not a hand-selected reduced test team."""
import copy
import json
from pathlib import Path

import pytest

from pantheon.apps.dependency_assembly import AssemblyError, _methods
from pantheon.apps.tool_profiles import compile_tool_profile, _type


def test_types_never_execute_manifest_python(tmp_path):
    marker = tmp_path/'executed'
    for value in (f"__import__('pathlib').Path({str(marker)!r}).touch()", 'typing.__dict__',
                  'str.__subclasses__()', 'MissingType', 'list[' * 150 + 'str' + ']' * 150):
        with pytest.raises((ValueError, SyntaxError)):
            _type(value)
    assert not marker.exists()


def shell_manifest():
    return json.loads((Path(__file__).resolve().parents[1]/'apps/shell/app.json').read_text())


def test_owned_shell_schema_preserves_optional_drain_and_removes_session_authority():
    source = shell_manifest()
    before = copy.deepcopy(source)
    profile, policy, dependency = compile_tool_profile(source, alias='shell', uses=['shell@1'],
        resource={'kind': 'shell', 'arguments': {'run_command': 'shell_id'}})
    assert source == before
    assert {f['name'] for f in profile['functions']} == {'run_command'}
    schema = profile['functions'][0]['parameters']
    assert set(schema['properties']) == {'command', 'timeout', 'max_output'}
    assert 'required' not in schema  # command=None drains the Agent's own session.
    assert set(policy['methods']) == {'run_command'}
    assert policy['resource']['arguments'] == {'run_command': 'shell_id'}
    assert not policy['methods']['run_command']['bound']
    assert dependency['binding'] == 'runtime'


@pytest.mark.parametrize('damage', ['interface', 'type', 'resource', 'param', 'duplicate', 'private'])
def test_invalid_complete_contract_fails_instead_of_dropping_tools(damage):
    source = shell_manifest()
    resource = {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}
    tool = next(t for t in source['provides']['tools'] if t['name'] == 'run_command')
    if damage == 'interface':
        source['provides']['interfaces'][0]['tools'].remove('run_command')
    elif damage == 'type':
        tool['params'][0]['type'] = 'NotAnExportedType'
    elif damage == 'resource':
        resource['arguments']['run_command'] = 'another_session'
    elif damage == 'param':
        tool['params'].append(tool['params'][0].copy())
    elif damage == 'duplicate':
        source['provides']['tools'].append(copy.deepcopy(tool))
    else:
        tool['params'].append({'name': 'context_variables', 'type': 'dict'})
    with pytest.raises(AssemblyError):
        compile_tool_profile(source, alias='shell', uses=['shell@1'], resource=resource)


def test_every_default_team_provider_has_its_complete_packaged_tool_face(tmp_path):
    from pantheon.apps.builtin.file.build_managed import build as files
    from pantheon.apps.builtin.notebook.build_managed import build as notebook
    from pantheon.apps.builtin.web.build_managed import build as web
    from pantheon.apps.builtin.evolution.build_managed import build as evolution
    from pantheon.apps.builtin.desktop.build_managed import build as desktop
    from pantheon.dependency_provider import DependencyToolProvider
    builds = {'file_manager': files, 'integrated_notebook': notebook,
              'web': web, 'evolution': evolution, 'desktop': desktop}
    profiles, policies, declarations = {}, {}, {}
    for name, builder in builds.items():
        kwargs = {'model_sampling': True, 'image_generation': True} if name == 'file_manager' else {}
        package = builder(tmp_path/name, 'darwin-arm64', **kwargs)
        manifest = json.loads((package/'app.json').read_text())
        uses = [f"{i['name']}@{i.get('version', 1)}" for i in manifest['provides']['interfaces']]
        profile, policy, dependency = compile_tool_profile(manifest, alias=name.replace('_', '-'), uses=uses)
        visible = {t['name'] for t in manifest['provides']['tools'] if not t.get('hidden')}
        assert {f['name'] for f in profile['functions']} == visible
        assert set(DependencyToolProvider._validate_functions(profile['functions'])) == visible
        _methods(dependency, manifest, policy['methods'])
        profiles[name], policies[name], declarations[manifest['id']] = profile, policy, dependency
    assert {'observe_images', 'generate_image', 'read_file', 'write_file'} <= set(policies['file_manager']['methods'])
    desktop_call = next(f for f in profiles['desktop']['functions'] if f['name'] == 'desktop_call')
    assert {'_action', '_args'} <= desktop_call['parameters']['properties'].keys()
    assert not any(name.startswith('tool_param') for name in desktop_call['parameters']['properties'])
    files_manifest = json.loads((tmp_path/'file_manager/app.json').read_text())
    assert {'list_files', 'manage_path'} <= {name for interface in files_manifest['provides']['interfaces']
                                           for name in interface['tools']}
    # Preserve the canonical team's members and selections. The composition
    # adds available providers, not deployment defaults granted to every member.
    from pantheon.factory.template_manager import TemplateManager
    from pantheon.chatroom.app_models import AppSettings
    templates = TemplateManager(settings=AppSettings(tmp_path/'settings', defaults={}, environment={}), seed_settings=False)
    recipes, tools, _ = templates.prepare_team(templates.get_template('default'))
    assert set(recipes) == {'leader', 'researcher', 'scientific_illustrator'}
    assert set(tools) - {'task', 'think'} == set(builds) | {'shell'}
