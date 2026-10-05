"""Run ordinary App recipes on an owned local Fleet profile.

Opt-in product host; no Agent implementation imports or ambient Fleet discovery.
The launcher keeps healthy infrastructure available if startup/drain needs explicit
recovery. Only a verified clean stop permits a new automatically composed cycle.
"""
import argparse
import asyncio
import base64
from contextlib import AsyncExitStack
import hashlib
import json
from pathlib import Path
import signal

from pantheon.apps.dependency_assembly import AssemblyError, DependencyAuthority, DependencyStarter, _copy, _matches, NAME
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_restart import plan_restart
from pantheon.apps.deployment_stop import AppDeploymentStop
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact, CHUNK_SIZE
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.bootstrap import ModelServiceBootstrap, digest
from pantheon.models.credentials import RemoteModelCredentialVault
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.manager import ModelServiceManager
from .app_preset import startup_recipe, _unique_fields
from .local_fleet import LocalFleet, LocalFleetBinaries, _private_secret


def private_json(path):
    path = Path(path)
    OwnerJournal(path.parent)._private(path)
    with path.open('rb') as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise AssemblyError('Local profile input exceeds its limit')
    return json.loads(raw, object_pairs_hook=_unique_fields)


def manifest(value):
    value = _copy(value)
    if (not isinstance(value, dict) or set(value) != {'protocol', 'packages', 'apps', 'model_apps'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or not isinstance(value['packages'], dict) or not 1 <= len(value['packages']) <= 24
            or not isinstance(value['apps'], dict) or not 1 <= len(value['apps']) <= 16
            or not isinstance(value['model_apps'], dict) or len(value['model_apps']) > 8
            or value['apps'].keys() & value['model_apps'].keys()):
        raise AssemblyError('Supply a bounded local App profile manifest')
    for name, package in value['packages'].items():
        if (not _matches(NAME, name) or not isinstance(package, dict) or set(package) != {'path', 'revision'}
                or not isinstance(package['path'], str) or not Path(package['path']).is_absolute()
                or not _matches(r'[a-f0-9]{64}', package['revision'])):
            raise AssemblyError('Local packages need absolute paths and exact artifact digests')
    targets = dict(value['apps'])
    for name, item in value['model_apps'].items():
        if not isinstance(item, dict) or set(item) != {'app', 'deployment_id', 'name', 'models'}:
            raise AssemblyError('Supply explicit attached model publications')
        targets[name] = item['app']
    for name, app in targets.items():
        if (not _matches(NAME, name) or not isinstance(app, dict)
                or set(app) != {'package', 'scope', 'components', 'bindings'}
                or not isinstance(app['package'], str) or app['package'] not in value['packages']):
            raise AssemblyError('Local Apps need a declared package and ordinary configuration/bindings')
    if {app['package'] for app in targets.values()} != set(value['packages']):
        raise AssemblyError('Include exactly the packages used by this local profile')
    return value


def local_values(value, context):
    if isinstance(value, dict):
        if '$local' in value:
            key = value['$local']
            if set(value) != {'$local'} or not isinstance(key, str) or key not in context:
                raise AssemblyError('Use an exact declared local profile value')
            return _copy(context[key])
        return {key: local_values(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [local_values(item, context) for item in value]
    return value


class LocalAppProfile(OwnerJournal):
    """Composition lifetime, under the enclosing LocalFleet profile lock.

    The caller retains the running Fleet on recoverable errors and must call
    stop to completion before closing it. Unexpected process death does not
    authorize rebasing an incomplete intent onto fresh authority coordinates.
    """
    error_type = AssemblyError

    def __init__(self, runtime, spec, resolver):
        self.runtime, self.info = runtime, runtime.coordinates
        self.spec, self.resolver = manifest(spec), resolver
        self.root = runtime.root / 'app-profile'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self._private(self.root, directory=True)
        self.path = self.root / 'current.json'
        self.wire = FleetLifecycle(resolver)
        credential = RuntimeCredential(self.info.controller, _private_secret(runtime.root / 'owner.key'))
        self.credential = credential
        self.ref = 'node-secret://profile-owner-' + hashlib.sha256(self.info.controller.encode()).hexdigest()[:16]
        self.authority = DependencyAuthority(credential=credential, tls_context=self.info.tls_context(),
                                             rpc_origin=self.info.controller)
        self.deploy = AppDeployment(DependencyStarter(self.wire, self.root/'starts', self.authority), self.root/'deployments')
        self.directory = LocalModelDirectory(self.root/'models', owner=self.info.fleet_id)
        self.manager = ModelServiceManager(client=self.directory, resolver=resolver)
        self.bootstrap = ModelServiceBootstrap(self.deploy, self.manager, self.root/'model-starts')
        self.stopper = AppDeploymentStop(self.deploy, self.root/'stops')
        self._staged = False
        self._record = None

    def _load(self):
        self._private(self.path)
        with self.path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Invalid local profile checkpoint')
        record = json.loads(raw, object_pairs_hook=_unique_fields)
        if (not isinstance(record, dict) or set(record) != {'protocol', 'cycle', 'manifest_hash', 'origin',
                'node_id', 'phase', 'recipe', 'models', 'workspace', 'ca_hash'}
                or type(record['protocol']) is not int or record['protocol'] != 1
                or type(record['cycle']) is not int or not 1 <= record['cycle'] < 1_000_000
                or record['phase'] not in ('starting', 'ready', 'stopping', 'stopped')
                or record['manifest_hash'] != digest(self.spec) or record['node_id'] != self.info.node_id
                or record['workspace'] != str(self.runtime.workspace)
                or record['ca_hash'] != hashlib.sha256(self.info.ca_certificate.read_bytes()).hexdigest()
                or not isinstance(record['models'], dict)
                or record['models'].keys() - self.spec['model_apps'].keys()):
            raise AssemblyError('Local profile changed; inspect its original composition')
        startup_recipe(record['recipe'])
        if (record['recipe']['operation_id'] != 'local-profile-' + str(record['cycle'])
                or set(record['recipe']['apps']) != set(self.spec['apps'])
                or set(record['recipe'].get('model_apps', {})) != set(self.spec['model_apps'])):
            raise AssemblyError('Local profile operation or App set changed')
        if record['recipe']['owner'] != self.info.fleet_id:
            raise AssemblyError('Local profile belongs to another Fleet owner')
        if record['phase'] != 'stopped' and record['origin'] != self.info.controller:
            raise AssemblyError('Previous local startup/stop did not finish; explicit recovery is required before reopening')
        return record

    def status(self):
        record = self._record
        return dict(state=record['phase'] if record else 'unopened',
                    cycle=record['cycle'] if record else 0, profile=str(self.runtime.root))

    def _render(self, cycle, generations, stopped):
        context = dict(controller=self.info.controller, trust_roots_pem=self.info.ca_certificate.read_text(),
            directory_root=str(self.directory.root), workspace=str(self.runtime.workspace),
            owner_credential={'ref': self.ref, 'endpoint': self.info.controller})
        def app(name, value):
            return dict(node_id=self.info.node_id, revision=self.spec['packages'][value['package']]['revision'],
                generation=generations.get(name, 0), scope=value['scope'],
                components=local_values(value['components'], context), bindings=local_values(value['bindings'], context))
        value = dict(owner=self.info.fleet_id, operation_id='local-profile-' + str(cycle),
                     apps={name: app(name, v) for name, v in self.spec['apps'].items()})
        if self.spec['model_apps']:
            providers = {}
            for name, v in self.spec['model_apps'].items():
                providers[name] = {**v, 'app': app(name, v['app'])}
                if name in stopped: providers[name]['restart_from'] = stopped[name]
            value.update(kind='model-services', model_apps=providers)
        return startup_recipe(value)

    def _consumer_id(self, recipe):
        return self.bootstrap.child_id(recipe, 'consumers') if recipe.get('kind') else recipe['operation_id']

    async def _open(self):
        if self._record is not None: return
        old = self._load() if self.path.exists() or self.path.is_symlink() else None
        await self.directory.initialize()
        if old is not None and old['phase'] != 'stopped':
            self._record = old
            return
        cycle, generations, stopped = (old['cycle'] + 1 if old else 1), {}, {}
        if old:
            if set(old['models']) != set(self.spec['model_apps']):
                raise AssemblyError('Stopped local profile is missing its model receipts')
            for role, names in (('consumers', self.spec['apps']), ('providers', self.spec['model_apps'])):
                if not names: continue
                source_id = (self._consumer_id(old['recipe']) if role == 'consumers'
                             else self.bootstrap.child_id(old['recipe'], role))
                new_id = 'local-profile-' + str(cycle)
                if old['recipe'].get('kind'):
                    new_id = self.bootstrap.child_id({'owner': self.info.fleet_id, 'operation_id': new_id}, role)
                planned = await plan_restart(self.deploy, owner=self.info.fleet_id, source_operation_id=source_id,
                                             operation_id=new_id, apps=list(names))
                generations.update({name: app['generation'] for name, app in planned['apps'].items()})
            for name, row in old['models'].items():
                if row['state'] != 'stopped' or await self.directory.deployment(row['deployment_id']) != row:
                    raise AssemblyError('Stopped model publication changed; review the profile before reopening')
                stopped[name] = row
        recipe = self._render(cycle, generations, stopped)
        record = dict(protocol=1, cycle=cycle, manifest_hash=digest(self.spec), origin=self.info.controller,
                      node_id=self.info.node_id, phase='starting', recipe=recipe, models={},
                      workspace=str(self.runtime.workspace), ca_hash=hashlib.sha256(self.info.ca_certificate.read_bytes()).hexdigest())
        await self._checkpoint(self.path, record)
        self._record = record

    async def _stage(self):
        if self._staged: return
        state = await self.wire.status(self.info.node_id)
        if state.get('owner') != self.info.fleet_id or state.get('node_id') != self.info.node_id:
            raise AssemblyError('Local package target does not belong to this profile')
        for package in self.spec['packages'].values():
            revision = package['revision']
            # An immutable installed artifact already proves the selected code.
            # Reopening must not rebuild/upload it or repeat its install hooks.
            if state['installations'].get(revision, {}).get('state') == 'installed':
                continue
            data, built = await asyncio.to_thread(build_artifact, Path(package['path']))
            if built != revision:
                raise AssemblyError('Local App package changed; build and review a new profile version')
            for offset in range(0, len(data), CHUNK_SIZE):
                await self.wire._request(self.info.node_id, 'stage', digest=revision, offset=offset,
                    data=base64.b64encode(data[offset:offset+CHUNK_SIZE]).decode())
        await RemoteModelCredentialVault(self.wire, owner=self.info.fleet_id, node_id=self.info.node_id).ensure_async(
            self.ref, self.credential.endpoint, self.credential.key)
        self._staged = True

    async def advance(self):
        await self._open()
        record = self._record
        if record['phase'] in ('stopping', 'stopped'):
            raise AssemblyError('Resume the current profile stop before another start')
        await self._stage()
        recipe = record['recipe']
        runner = self.bootstrap if recipe.get('kind') else self.deploy
        result = await runner.advance(**recipe)
        if result['state'] == 'ready':
            if recipe.get('kind'):
                registered = self.bootstrap._load(self.bootstrap._path(recipe['operation_id']))['registered']
                rows = {}
                for name, entry in recipe['model_apps'].items():
                    row = await self.directory.deployment(entry['deployment_id'])
                    if digest(row) != registered[name]['directory_hash']:
                        raise AssemblyError('Model publication changed before profile readiness')
                    rows[name] = row
                record['models'] = rows
            record['phase'] = 'ready'
            await self._checkpoint(self.path, record)
        return self.status()

    async def stop(self):
        await self._open()
        record = self._record
        if record['phase'] == 'starting':
            raise AssemblyError('Startup is incomplete; inspect and resume it before closing this profile')
        if record['phase'] == 'ready':
            record['phase'] = 'stopping'
            await self._checkpoint(self.path, record)
        if record['phase'] == 'stopping':
            stopped = await self.stopper.advance(owner=self.info.fleet_id,
                operation_id='profile-stop-' + str(record['cycle']), source_operation_id=self._consumer_id(record['recipe']),
                apps=list(self.spec['apps']))
            if stopped['state'] != 'stopped': return self.status()
            for name, previous in record['models'].items():
                row = await self.directory.deployment(previous['deployment_id'])
                def identity(value):
                    return {**value, 'state': '', 'revision': 0,
                            'binding': {**value['binding'], 'generation': 0}}
                if (identity(row) != identity(previous) or row['binding']['generation'] not in (
                        previous['binding']['generation'], previous['binding']['generation'] + 1)
                        or row['state'] not in ('ready', 'stopping', 'stopped')):
                    raise AssemblyError('Model changed during profile shutdown; inspect Model Services')
                if previous['state'] == 'stopped':
                    if row != previous: raise AssemblyError('Stopped model receipt changed')
                    continue
                if row['state'] == 'stopped':
                    state = await self.wire.status(self.info.node_id)
                    _, is_stopped = self.manager.bound_instance(state, row['binding'], 'model-' + row['deployment_id'])
                    if not is_stopped or row['binding']['generation'] != previous['binding']['generation'] + 1:
                        raise AssemblyError('Model stop is not confirmed by Fleet')
                    record['models'][name] = row
                else:
                    record['models'][name] = await self.manager.set_running(row['deployment_id'], False)
                await self._checkpoint(self.path, record)
            record['phase'] = 'stopped'
            await self._checkpoint(self.path, record)
        state = await self.wire.status(self.info.node_id)
        if any(i.get('state') != 'stopped' or i.get('resources') or i.get('reservations')
               for i in state['instances'].values()):
            raise AssemblyError('Other Apps still use this profile; keep Fleet running until they are stopped')
        await self._checkpoint(self.path, record)
        return self.status()


async def maintain_dependencies(session, report, *, interval=30):
    """Keep the original grants alive; a failed observation never restarts Apps."""
    previous = None
    while True:
        try:
            summary = await session.deploy.starter.reconcile_once()
            warning = {key: summary.get(key, 0) for key in ('expired', 'invalid', 'deferred')}
        except Exception:
            # Root-level storage failures occur outside per-attempt reconciliation.
            # Keep serving and retry the same receipts, without exposing private
            # paths, keys or exception text through the public status stream.
            warning = {'unavailable': True}
        if warning != previous and (any(warning.values()) or previous is not None and any(previous.values())):
            await report({**session.status(), 'dependency_maintenance': warning})
        previous = warning
        await asyncio.sleep(interval)


async def _cancel_task(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def serve(root, binaries, workspace, spec, *, on_status=None, commands=None):
    """Interactive local host; retry/stop are explicit commands, not crash healing."""
    import nats
    commands = commands or asyncio.Queue()
    async def report(value):
        if on_status: await on_status(value)
        else: print(json.dumps(value), flush=True)
    async with LocalFleet(root, binaries, workspace=workspace) as runtime, AsyncExitStack() as cleanup:
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        cleanup.push_async_callback(nc.close)
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(runtime.workspace), connection=nc)
        cleanup.push_async_callback(resolver.close)
        session = LocalAppProfile(runtime, spec, resolver)
        watcher = asyncio.create_task(runtime.wait())
        cleanup.push_async_callback(_cancel_task, watcher)
        maintenance = asyncio.create_task(maintain_dependencies(session, report))
        cleanup.push_async_callback(_cancel_task, maintenance)
        async def check_workers():
            for task in (watcher, maintenance):
                if task.done():
                    await task
                    raise RuntimeError('Local profile supervision ended unexpectedly')
        command = 'start'
        while True:
            try:
                if command in ('start', 'retry', 'stop'):
                    stopping = command == 'stop' or session.status()['state'] in ('stopping', 'stopped')
                    while True:
                        await check_workers()
                        status = await (session.stop() if stopping else session.advance())
                        if status['state'] in ('ready', 'stopped'): break
                        await asyncio.sleep(.1)
                    await report(status)
                    if status['state'] == 'stopped': return
                else:
                    await report(session.status())
            except Exception as error:
                await check_workers()
                await report({**session.status(), 'needs_attention': True,
                    'error': str(error) if isinstance(error, AssemblyError) else
                    'Operation outcome is unknown; inspect the profile logs and retry the same operation'})
            waiting = asyncio.create_task(commands.get())
            try:
                await asyncio.wait([watcher, maintenance, waiting], return_when=asyncio.FIRST_COMPLETED)
                await check_workers()
                command = await waiting
            finally:
                await _cancel_task(waiting)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'macOS/Linux: Ctrl-C or SIGTERM requests an ordered App shutdown. '
        'SIGUSR1 retries the current startup/stop operation after an error. '
        'Wait for a stopped status before exiting; incomplete startup requires '
        'recovery. This host does not open a REPL or Desktop window.'))
    for name in ('profile', 'workspace', 'manifest', 'controller', 'broker', 'runner'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    spec = manifest(private_json(args.manifest))
    binaries = LocalFleetBinaries(*(Path(getattr(args, name)).expanduser().absolute() for name in ('controller', 'broker', 'runner')))
    async def run_command():
        commands = asyncio.Queue()
        loop = asyncio.get_running_loop()
        signals = ((signal.SIGINT, 'stop'), (signal.SIGTERM, 'stop'), (signal.SIGUSR1, 'retry'))
        for sig, command in signals: loop.add_signal_handler(sig, commands.put_nowait, command)
        try:
            await serve(args.profile, binaries, args.workspace, spec, commands=commands)
        finally:
            for sig, _ in signals: loop.remove_signal_handler(sig)
    asyncio.run(run_command())


if __name__ == '__main__':
    main()
