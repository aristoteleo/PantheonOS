"""Prepared MCP tools on the ordinary App RPC host.

The immutable App release owns its export contract. Fleet supplies explicit
server configuration and endpoint-paired credentials. Agent receives only
ordinary dependency methods, never a gateway URL or a management credential.
The legacy MCPGatewayToolSet remains a separate entry in the same App sources.
"""
import asyncio
from collections.abc import Mapping
from contextlib import AsyncExitStack, nullcontext
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,127}\Z')
PRIVATE = {'context_variables', '_call_agent', '_background'}
LIMIT = 512 * 1024


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def bounded(value, limit=LIMIT):
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded.encode()) > limit:
        raise ValueError('MCP payload exceeds the App limit')
    return json.loads(encoded)


def local_refs(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == '$id':
                raise ValueError('MCP schemas cannot change reference bases')
            if key in {'$ref', '$dynamicRef'} and (not isinstance(item, str) or not item.startswith('#')):
                raise ValueError('MCP schemas cannot resolve external resources')
            local_refs(item)
    elif isinstance(value, list):
        for item in value:
            local_refs(item)


def validate_exports(exports):
    """Reviewable, static RPC names compatible with DependencyToolProvider."""
    try:
        exports = bounded(plain(exports))
        if not isinstance(exports, dict) or not 1 <= len(exports) <= 64:
            raise ValueError
        for name, spec in exports.items():
            if (not NAME.fullmatch(name) or name in {'get_uri', 'list_servers', 'add_server', 'remove_server',
                    'start_servers', 'stop_servers', 'restart_server'}
                    or not isinstance(spec, dict) or set(spec) != {'server', 'tool', 'description', 'parameters'}
                    or not NAME.fullmatch(spec['server']) or not isinstance(spec['tool'], str)
                    or not 1 <= len(spec['tool']) <= 256 or not isinstance(spec['description'], str)):
                raise ValueError
            schema = spec['parameters']
            props, required = schema['properties'], schema.get('required', [])
            if (schema.get('type') != 'object' or not isinstance(props, dict) or len(props) > 64
                    or props.keys() & PRIVATE or not all(NAME.fullmatch(key) and isinstance(value, dict)
                                                        for key, value in props.items())
                    or not isinstance(required, list) or not set(required) <= props.keys()
                    or schema.get('additionalProperties', False) is not False):
                raise ValueError
            local_refs(schema)
            Draft202012Validator.check_schema(schema)
            schema['additionalProperties'] = False
        return exports
    except (ValueError, TypeError, KeyError, RecursionError, SchemaError):
        raise ValueError('Invalid MCP App export contract') from None


def endpoint(value):
    p = urlsplit(value)
    if p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError
    p.port
    return value


class ScopedMCP:
    def __init__(self, exports, configuration, *, client_factory=None, model_client_factory=None):
        self.exports = validate_exports(exports)
        try:
            values = plain(configuration.values)
            if (configuration.component != 'backend' or not configuration.owner
                    or set(values) != {'mcp'} or not {'protocol', 'servers'} <= set(values['mcp'])
                    or set(values['mcp']) - {'protocol', 'servers', 'sampling'}
                    or type(values['mcp']['protocol']) is not int or values['mcp']['protocol'] != 1):
                raise ValueError
            servers = values['mcp']['servers']
            if not isinstance(servers, dict) or set(servers) != {s['server'] for s in self.exports.values()}:
                raise ValueError
            credentials, used = configuration.credentials, set()
            sampling = values['mcp'].get('sampling')
            if 'sampling' in values['mcp']:
                if not isinstance(sampling, dict) or not isinstance(sampling.get('credential'), str):
                    raise ValueError
                used.add(sampling['credential'])
            self.servers = {}
            for name, spec in servers.items():
                if not isinstance(spec, dict):
                    raise ValueError
                spec = dict(spec)
                if spec.get('transport') == 'http':
                    if set(spec) - {'transport', 'url', 'credential'} or not {'url', 'transport'} <= set(spec):
                        raise ValueError
                    endpoint(spec['url'])
                    if 'credential' in spec:
                        alias = spec.pop('credential')
                        credential = credentials[alias]
                        if credential.endpoint != spec['url']:
                            raise ValueError
                        spec['token'] = credential.key
                        used.add(alias)
                elif spec.get('transport') == 'stdio':
                    if (not {'transport', 'command', 'cwd', 'env'} <= set(spec)
                            or set(spec) - {'transport', 'command', 'cwd', 'env', 'env_credentials'}
                            or not isinstance(spec['command'], list) or not spec['command']
                            or len(spec['command']) > 128
                            or not all(isinstance(arg, str) and '\0' not in arg for arg in spec['command'])
                            or not Path(spec['command'][0]).is_absolute()
                            or not isinstance(spec['cwd'], str) or not Path(spec['cwd']).is_absolute()
                            or not isinstance(spec['env'], dict)
                            or not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)
                                       and isinstance(v, str) and '\0' not in v for k, v in spec['env'].items())):
                        raise ValueError
                    secret_env = spec.pop('env_credentials', {})
                    if (not isinstance(secret_env, dict) or len(secret_env) > 64
                            or secret_env.keys() & spec['env'].keys()):
                        raise ValueError
                    for variable, binding in secret_env.items():
                        if (not isinstance(variable, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', variable)
                                or not isinstance(binding, dict) or set(binding) != {'credential', 'endpoint'}
                                or not isinstance(binding['credential'], str)):
                            raise ValueError
                        endpoint(binding['endpoint'])
                        credential = credentials[binding['credential']]
                        if (credential.endpoint != binding['endpoint'] or not isinstance(credential.key, str)
                                or not credential.key or '\0' in credential.key):
                            raise ValueError
                        spec['env'][variable] = credential.key
                        used.add(binding['credential'])
                else:
                    raise ValueError
                self.servers[name] = spec
            if used != set(credentials):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Invalid prepared MCP server configuration') from None
        self._sampling = None
        if sampling is not None:
            from .sampling import ModelSampling
            self._sampling = ModelSampling(sampling, credentials, client_factory=model_client_factory)
        self._factory = client_factory or self._client
        self._clients = {}
        self._session = None
        self._ready = None
        self._release = asyncio.Event()
        self._accepting = False
        self._active = set()
        self._slots = asyncio.Semaphore(8)
        self._closing = None

    @staticmethod
    def _client(spec):
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
        if spec['transport'] == 'stdio':
            transport = StdioTransport(spec['command'][0], spec['command'][1:],
                cwd=spec['cwd'], env=spec['env'], keep_alive=False)
        else:
            import httpx
            def http_client(headers=None, timeout=None, auth=None, follow_redirects=False):
                return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth,
                                         trust_env=False, follow_redirects=False)
            transport = StreamableHttpTransport(spec['url'],
                headers={'Authorization': 'Bearer ' + spec['token']} if 'token' in spec else None,
                httpx_client_factory=http_client)
        # No ambient Agent sampling, OAuth login, roots or elicitation callbacks.
        return Client(transport, timeout=60, init_timeout=20)

    async def _run(self):
        # AnyIO contexts must enter and exit on the SAME task. Keep connections
        # throughout App lifetime so stateful stdio tools do not restart per call.
        try:
            async with AsyncExitStack() as stack:
                for name, spec in self.servers.items():
                    client = self._factory(spec)
                    if self._sampling is not None:
                        from functools import partial
                        from mcp.types import SamplingCapability
                        client.set_sampling_callback(partial(self._sampling.sample, name),
                                                     sampling_capabilities=SamplingCapability())
                    client = await stack.enter_async_context(client)
                    tools = {t.name: t.inputSchema for t in await client.list_tools()}
                    for export in self.exports.values():
                        if export['server'] != name:
                            continue
                        actual = bounded(tools[export['tool']])
                        actual.setdefault('additionalProperties', False)
                        if actual != export['parameters']:
                            raise ValueError('MCP tool schema changed; review a new App contract')
                    self._clients[name] = client
                self._accepting = True
                self._ready.set_result(None)
                await self._release.wait()
        except BaseException as error:
            self._accepting = False
            if not self._ready.done():
                self._ready.set_exception(RuntimeError('MCP App could not establish its reviewed tool contract'))
            if isinstance(error, asyncio.CancelledError):
                raise
            # Upstream exceptions may contain endpoint credentials or server logs.
            raise RuntimeError('MCP App connection failed; inspect provider logs locally') from None
        finally:
            self._clients.clear()
            self._accepting = False

    async def start(self):
        if self._session is not None or self._closing is not None:
            raise RuntimeError('MCP App has already started or stopped')
        self._ready = asyncio.get_running_loop().create_future()
        self._session = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout=30)
        except BaseException:
            await self.close()
            raise

    async def call(self, name, arguments):
        if not self._accepting:
            raise RuntimeError('MCP App is not accepting calls')
        try:
            spec = self.exports[name]
            arguments = bounded(arguments)
            Draft202012Validator(spec['parameters']).validate(arguments)
        except (KeyError, ValueError, TypeError, RecursionError, ValidationError):
            raise ValueError('MCP tool or arguments are outside the reviewed contract') from None
        if len(self._active) >= 32:
            raise RuntimeError('MCP App is busy; no tool call was admitted')
        async def invoke():
            async with self._slots:
                # Once admitted, drain even if the consumer disconnected.
                try:
                    with self._sampling.admit(spec['server']) if self._sampling else nullcontext():
                        result = await self._clients[spec['server']].call_tool(
                            spec['tool'], arguments, timeout=60, raise_on_error=False)
                    return bounded({'content': [block.model_dump(mode='json', by_alias=True, exclude_none=True)
                                                for block in result.content],
                                    'structuredContent': result.structured_content, '_meta': result.meta,
                                    'isError': result.is_error}, LIMIT - 8192)
                except Exception:
                    raise RuntimeError('MCP tool call failed; outcome may be unknown. It was not retried.') from None
        task = asyncio.create_task(invoke())
        self._active.add(task)
        task.add_done_callback(self._active.discard)
        return await drain(task)

    async def close(self):
        self._accepting = False
        if self._closing is None:
            async def finish():
                if self._active:
                    await asyncio.gather(*tuple(self._active), return_exceptions=True)
                try:
                    if self._sampling is not None:
                        await self._sampling.close()
                finally:
                    self._release.set()
                    if self._session is not None:
                        if self._ready is not None and not self._ready.done():
                            self._session.cancel()
                        await asyncio.gather(self._session, return_exceptions=True)
                        if self._ready is not None and self._ready.done() and not self._ready.cancelled():
                            self._ready.exception()
            self._closing = asyncio.create_task(finish())
        await drain(self._closing)


async def drain(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def register(ctx):
    from pantheon.apps.runtime_config import load_runtime_configuration
    exports = json.loads(Path(__file__).with_name('exports.json').read_text())
    host = ScopedMCP(exports, load_runtime_configuration(required=True))
    ctx.require_rpc_token = True
    ctx.on_cleanup(host.close)
    ctx.before_stop = host.close
    await host.start()
    for name in host.exports:
        def make_method(name):
            async def invoke(**arguments):
                return await host.call(name, arguments)
            invoke.__name__ = name
            return invoke
        ctx.method(make_method(name))
        ctx.concurrent_methods.add(name)
