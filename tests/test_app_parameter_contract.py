"""A legacy manifest, generated release and real ToolSet agree on defaults."""
from copy import deepcopy
import json

import pytest

from pantheon.apps.compat import check_compat
from pantheon.apps.reflect import reflect_toolset_class, reflect_toolset_instance
from pantheon.apps.registry import refresh_manifest
from pantheon.apps.schema import parse_manifest
from pantheon.toolset import ToolSet, tool


class Arguments(ToolSet):
    @tool
    async def echo(self, command: str, nullable: str | None = None,
                   enabled: bool = False, count: int = 0, label: str = '') -> dict:
        return dict(command=command, nullable=nullable, enabled=enabled, count=count, label=label)


def legacy_manifest():
    tools = []
    for name in ('echo', 'list_tools'):
        fn = getattr(Arguments, name)
        # Reproduce the old emitter's wire-sentinel-as-default bug and its
        # omission of None. Existing published files must remain readable.
        params = [{'name': p['name'], 'type': p['type'], 'required': False,
                   **({'default': p['default']} if p['default'] is not None else {})}
                  for p in fn._tool_desc['inputs']]
        tools.append({'name': name, 'params': params, 'hidden': fn._exclude})
    return {'id': 'arguments', 'name': 'Arguments', 'version': '1.0.0',
            'entry': {'backend': 'fixture:Arguments'}, 'provides': {'tools': tools,
            'interfaces': [{'name': 'echo', 'version': 1, 'tools': ['echo']}]}}


@pytest.mark.asyncio
async def test_manifest_requiredness_matches_real_calls_and_wire():
    instance = Arguments('test')
    before = deepcopy(Arguments.echo._tool_desc)
    sources = [parse_manifest(legacy_manifest()).provides.tools,
               reflect_toolset_class(Arguments), reflect_toolset_instance(instance)]
    for tools in sources:
        by_name = {p.name: p for p in next(t for t in tools if t.name == 'echo').params}
        assert by_name['command'].required and by_name['command'].default is None
        for key, default in {'nullable': None, 'enabled': False, 'count': 0, 'label': ''}.items():
            assert not by_name[key].required and by_name[key].default == default
    with pytest.raises(TypeError, match='command'):
        await instance.echo()
    assert await instance.echo(command='hello') == dict(command='hello', nullable=None, enabled=False, count=0, label='')
    assert Arguments.echo._tool_desc == before  # Do not mutate the existing bus protocol.
    assert before['inputs'][0]['default'] == 'not_defined'


def test_emit_preserves_null_and_old_release_is_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr('pantheon.apps.registry.backend_class', lambda _: Arguments)
    old = legacy_manifest()
    path = tmp_path/'app.json'
    path.write_text(json.dumps(old))
    assert refresh_manifest(tmp_path)
    emitted = json.loads(path.read_text())
    params = {p['name']: p for p in next(t for t in emitted['provides']['tools'] if t['name'] == 'echo')['params']}
    assert 'default' not in params['command'] and params['command'].get('required', True)
    assert params['nullable']['default'] is None and params['nullable']['required'] is False
    assert 'not_defined' not in path.read_text()
    assert refresh_manifest(tmp_path) is False
    report = check_compat(parse_manifest(old), parse_manifest(emitted))
    assert report.ok and not report.breaking and not report.additive
    # A genuinely optional -> required change still breaks the declared
    # interface and requires both the interface and App version to change.
    params['nullable'].pop('default')
    params['nullable']['required'] = True
    report = check_compat(parse_manifest(old), parse_manifest(emitted))
    assert not report.ok and report.required_bump == 'major'
    assert any('nullable became required' in item for item in report.breaking)
    assert any('interface echo@1' in item for item in report.violations)
