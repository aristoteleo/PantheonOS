"""Compile an explicitly selected App contract into consumer tool schemas.

This owner-side step reads manifest data only. It neither imports the provider
implementation nor evaluates Python from its type annotations. The resulting
policy still needs ordinary Fleet admission and per-consumer grant issuance.
"""
import ast
import typing

from .dependency_assembly import AssemblyError, NAME, RPC, ARGUMENT, _copy, _matches, _methods
from .schema import parse_manifest
from pantheon.funcdesc.desc import Description, Value, NotDef
from pantheon.funcdesc.pydantic import desc_to_pydantic


_TYPES = {name: value for name, value in vars(typing).items()
          if name in {'Any', 'Optional', 'Union', 'Literal', 'List', 'Dict', 'Tuple', 'Set'}}
_TYPES.update(str=str, int=int, float=float, bool=bool, list=list, dict=dict,
              tuple=tuple, set=set, object=typing.Any, NoneType=type(None))


def _type(value):
    if value is None:
        return typing.Any
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Invalid manifest type')
    tree = ast.parse(value, mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError('Manifest type is too complex')

    def read(node):
        if isinstance(node, ast.Name) and node.id in _TYPES:
            return _TYPES[node.id]
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == 'typing' and node.attr in _TYPES):
            return _TYPES[node.attr]
        if isinstance(node, ast.Constant) and node.value is None:
            return type(None)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return typing.Union[read(node.left), read(node.right)]
        if isinstance(node, ast.Subscript):
            base = read(node.value)
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base is typing.Literal:
                if not all(isinstance(arg, ast.Constant) and
                           (arg.value is None or type(arg.value) in (str, int, bool)) for arg in args):
                    raise ValueError('Invalid literal')
                return typing.Literal[tuple(arg.value for arg in args)]
            if base not in (typing.Optional, typing.Union, typing.List, typing.Dict,
                            typing.Tuple, typing.Set, list, dict, tuple, set):
                raise ValueError('Invalid generic type')
            values = tuple(Ellipsis if isinstance(arg, ast.Constant) and arg.value is Ellipsis
                           else read(arg) for arg in args)
            return base[values[0] if len(values) == 1 else values]
        raise ValueError('Unsupported manifest type')

    return read(tree.body)


def compile_tool_profile(manifest, *, alias, uses, resource=None, service_methods=None):
    """Return (profile, allocation policy, runtime dependency declaration).

    Every non-hidden tool must be covered by the selected versioned interfaces.
    Missing coverage fails rather than silently reducing the Agent's tool face.
    Hidden lifecycle/GUI methods require an explicit service_methods selection;
    their schemas authorize host calls without adding them to the LLM menu.
    Resource arguments are removed from the model schema and supplied by the
    existing owner-managed resource-session allocator.
    """
    try:
        app = parse_manifest(manifest)
        if not _matches(NAME, alias) or not isinstance(uses, list) or not uses or len(set(uses)) != len(uses):
            raise ValueError
        service_methods = [] if service_methods is None else service_methods
        hidden = {tool.name for tool in app.provides.tools if tool.hidden}
        if (not isinstance(service_methods, list) or len(service_methods) > 64
                or not all(isinstance(name, str) for name in service_methods)
                or len(set(service_methods)) != len(service_methods) or not set(service_methods) <= hidden):
            raise ValueError
        tools = [tool for tool in app.provides.tools if not tool.hidden or tool.name in service_methods]
        if not 1 <= len(tools) <= 64 or len({tool.name for tool in tools}) != len(tools):
            raise ValueError
        resource = _copy(resource) if resource is not None else None
        arguments = {}
        if resource is not None:
            if (set(resource) != {'kind', 'arguments'} or not _matches(RPC, resource['kind'])
                    or not isinstance(resource['arguments'], dict) or not resource['arguments']
                    or not resource['arguments'].keys() <= {tool.name for tool in tools}):
                raise ValueError
            arguments = resource['arguments']
        functions, services, methods, checked = [], [], {}, {}
        for tool in tools:
            if not _matches(RPC, tool.name):
                raise ValueError
            names = [p.name for p in tool.params]
            if (len(set(names)) != len(names) or not all(_matches(ARGUMENT, name) for name in names)
                    or set(names) & {'context_variables', '_call_agent', '_background'}):
                raise ValueError
            injected = arguments.get(tool.name)
            if tool.name in arguments and injected not in names:
                raise ValueError
            desc = Description()
            desc.name, desc.doc = tool.name, tool.description or ''
            desc.inputs = [Value(name=p.name, type_=_type(p.type), doc=p.description,
                                 default=NotDef if p.required else p.default)
                           for p in tool.params if p.name != injected]
            schema = desc_to_pydantic(desc)['inputs'].model_json_schema(by_alias=True)
            schema.pop('title', None)
            schema['additionalProperties'] = False
            (services if tool.hidden else functions).append(
                {'name': tool.name, 'description': desc.doc, 'parameters': schema})
            methods[tool.name] = {'arguments': [name for name in names if name != injected], 'bound': {}}
            checked[tool.name] = {**methods[tool.name], 'bound': {injected: '<resource>'} if injected else {}}
        dependency = {'range': app.version, 'uses': list(uses), 'binding': 'runtime'}
        _methods(dependency, app.model_dump(), checked)
        policy = {'app_id': app.id, 'provider': {'$app': alias, 'component': 'backend', 'port': 'http'},
                  'methods': methods}
        if resource is not None:
            policy['resource'] = resource
        profile = {'alias': alias, 'functions': functions}
        if services:
            profile['service_functions'] = services
        return profile, policy, dependency
    except (ValueError, TypeError, KeyError, AttributeError, SyntaxError, RecursionError):
        raise AssemblyError('Cannot compile the complete App tool profile; check types, interfaces and resource arguments') from None
