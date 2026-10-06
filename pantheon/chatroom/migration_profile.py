"""Owner-side local Agent migration using ordinary prepared App profiles.

The platform host never imports this module. It reserves destination admission
before fencing sources, retains the exact backup/plan on failure, and lets the
host stop prepared consumers without ever launching them. Normal CLI/Desktop
startup can subsequently open the committed data.
"""
import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import signal
import sys

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.dependency_binding_client import _drain
from .migration import legacy_source_roots, fence_legacy
from .migration_backup import (_encoded, _read_json, _destination, backup_legacy,
                               verify_backup, DEFAULT_MAX_BYTES)
from .migration_import import reserve_import, import_backup, abort_pending_import
from .migration_workspaces import RetainedWorkspaceConversion


class LocalAgentMigration:
    """One immutable reviewed request; private receipts are never public status."""
    def __init__(self, request, *, abort=False):
        required = {'protocol', 'operation', 'app', 'legacy', 'backup'}
        optional = {'retained_roots', 'model_selection', 'max_bytes'}
        if (not isinstance(request, dict) or not required <= request.keys()
                or request.keys() - required - optional or type(request['protocol']) is not int
                or request['protocol'] != 1 or not isinstance(request['legacy'], dict)
                or any(not isinstance(request[k], str) or not 0 < len(request[k]) <= 256
                       or any(ord(c) < 32 for c in request[k]) for k in ('operation', 'app'))
                or not isinstance(request['backup'], str) or not Path(request['backup']).is_absolute()
                or type(request.get('max_bytes', DEFAULT_MAX_BYTES)) is not int
                or request.get('max_bytes', DEFAULT_MAX_BYTES) <= 0):
            raise ValueError('Supply an explicit private Agent migration request')
        roots = request.get('retained_roots', [])
        if (not isinstance(roots, list) or len(roots) > 128
                or any(not isinstance(r, str) or not Path(r).is_absolute() for r in roots)):
            raise ValueError('Retained roots must be explicitly selected absolute directories')
        if 'model_selection' in request:
            mapping = request['model_selection']
            if (not isinstance(mapping, dict) or not {'selections', 'fleet_tiers'} <= mapping.keys()
                    or mapping.keys() - {'selections', 'fleet_tiers', 'dependency', 'templates', 'settings',
                                        'budget_choice', 'source_service_id'}):
                raise ValueError('Supply explicit saved model and quality-tier mappings')
        legacy_source_roots(request['legacy'])
        _destination(request['legacy'], request['backup'])
        self.request = json.loads(_encoded(request))
        self.abort = abort
        self.result = None

    def _import(self, *, target, configuration, owner, node_id, request_digest):
        request = self.request
        legacy = request['legacy']
        namespace = configuration['namespace']
        archive_root = Path(request['backup']).resolve()
        if archive_root.is_relative_to(target.resolve()) or target.resolve().is_relative_to(archive_root):
            raise ValueError('Keep the immutable backup separate from the Agent destination')
        reserve_import(legacy, target=target, namespace=namespace,
                       operation=request['operation'], request_digest=request_digest, aborting=self.abort)
        with fence_legacy(legacy, operation=request['operation'], target=target, namespace=namespace) as fence:
            if self.abort:
                abort_pending_import(fence=fence)
                return dict(root=str(target), state='aborted')
            archive = Path(request['backup'])/'snapshot'
            if archive.exists() or archive.is_symlink():
                intent = _read_json(Path(request['backup'])/'intent.json')
                if intent.get('fence') != fence.identity:
                    raise ValueError('Backup belongs to a different migration fence')
                backup = verify_backup(archive, digest=intent['sha256'])
                manifest = _read_json(archive/'manifest.json')
                if manifest['spec'] != legacy or manifest['fence'] != fence.identity:
                    raise ValueError('Backup belongs to a different migration request')
            else:
                backup = backup_legacy(legacy, fence=fence, directory=request['backup'],
                    max_bytes=request.get('max_bytes', DEFAULT_MAX_BYTES))
            conversions = {}
            if request.get('retained_roots'):
                profiles = configuration['dependencies']['profiles']['toolsets']
                conversions['retained_workspaces'] = RetainedWorkspaceConversion(
                    backup['directory'], digest=backup['sha256'], fence=fence, owner=owner,
                    source_node_id=node_id, roots=request['retained_roots'],
                    providers={name: {k: profiles[name][k] for k in ('alias', 'provider')}
                               for name in ('file_manager', 'shell')})
            if 'model_selection' in request:
                from .migration_models import ModelSelectionConversion
                conversions['model_selection'] = ModelSelectionConversion(backup['directory'],
                    digest=backup['sha256'], fence=fence, owner=owner, node_id=node_id,
                    **request['model_selection'])
                if conversions['model_selection'].describe()['models'] != configuration['models']:
                    raise ValueError('Prepared Agent models differ from the reviewed migration mapping')
            receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence, **conversions)
            return dict(root=str(target), state='imported', receipt=receipt, backup=backup)

    async def __call__(self, session):
        candidate = await session.prepared_app(self.request['app'])
        identity = candidate['identity']
        if identity['node_id'] != session.info.node_id:
            raise ValueError('This migration entry point requires local prepared Agent data')
        # Select the actual Agent package, not an arbitrary App with a similarly
        # named configuration value. Fleet owns the reserved instance identity.
        package = await session.wire.manifest(identity['node_id'], identity['revision'])
        if package['manifest']['id'] != 'agent':
            raise ValueError('Select a prepared Pantheon-Agent App')
        from .data_transition import INITIALIZATION_CAPABILITY
        capability = (package['manifest'].get('caps') or {}).get('agentDataInitialization')
        if (capability != INITIALIZATION_CAPABILITY or type(capability.get('protocol')) is not int):
            raise AssemblyError('Update the Agent release before migration: initialization reservation is unsupported')
        configuration = candidate['components']['backend']['values']['agent']
        from .app_data import AppProjects
        expected = AppProjects(self.request['legacy']['projects']).list_projects()
        actual = AppProjects(configuration['projects']).list_projects()
        actual_paths = {p['id']: p['path'] for p in actual}
        if any(actual_paths.get(p['id']) != p['path'] for p in expected):
            raise ValueError('Agent configuration must preserve the reviewed legacy projects')
        target = session.runtime.root/'node/apps'/session.info.fleet_id/'data'/identity['instance_id']/'agent'
        reviewed = dict(request=self.request, profile=session.spec,
                        target={k: v for k, v in identity.items() if k != 'generation'}, owner=session.info.fleet_id)
        digest = sha256(_encoded(reviewed)).hexdigest()
        # Cancellation must wait for the source fence/copy worker to finish;
        # never detach a writer while the host starts cleanup or another retry.
        try:
            self.result = await _drain(asyncio.create_task(asyncio.to_thread(self._import,
                target=target, configuration=deepcopy(configuration), owner=session.info.fleet_id,
                node_id=session.info.node_id, request_digest=digest)))
        except ValueError as error:
            raise AssemblyError(str(error)) from error
        return dict(state=self.result['state'], operation=self.request['operation'])


def main(argv=None):
    from pantheon.apps.local_agent import read_bundle, compose_profile
    from pantheon.platform.local_launch import read_launch
    from pantheon.platform.local_profile import private_json, serve
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Use SIGUSR1 to retry the same interrupted operation; Ctrl-C/SIGTERM drains '
        'the prepared profile. Sources remain fenced on failure. This command '
        'does not launch Agent or perform a post-cutover rollback.'))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--launch', help='Existing private local launch description')
    source.add_argument('--bundle', help='Packaged local Fleet and Agent release')
    for name in ('setup', 'profile', 'workspace', 'credentials'):
        parser.add_argument('--' + name)
    parser.add_argument('--request', required=True, help='Private reviewed migration request JSON')
    parser.add_argument('--abort', action='store_true',
        help='Abort this reserved/uncommitted import and release old sources; committed imports are refused')
    args = parser.parse_args(argv)
    launch = None
    if args.launch:
        if any(getattr(args, name) for name in ('setup', 'profile', 'workspace', 'credentials')):
            parser.error('--launch already specifies setup, profile, workspace and credentials')
        launch = read_launch(args.launch)
        for key in ('bundle', 'setup', 'profile', 'workspace', 'credentials'):
            setattr(args, key, launch.get(key))
    if not all((args.setup, args.profile, args.workspace)):
        parser.error('--bundle requires --setup, --profile and --workspace')
    workflow = LocalAgentMigration(private_json(args.request), abort=args.abort)
    binaries, entries = read_bundle(args.bundle)
    spec = compose_profile(entries, private_json(args.setup))
    if workflow.request['app'] not in spec['apps']:
        parser.error('Migration app must be present in the selected profile')
    credentials = None
    if args.credentials:
        from pantheon.platform.local_credentials import read_credentials
        credentials = read_credentials(args.credentials, spec, args.workspace)
    async def run():
        commands = asyncio.Queue()
        loop = asyncio.get_running_loop()
        signals = ((signal.SIGINT, 'stop'), (signal.SIGTERM, 'stop'), (signal.SIGUSR1, 'retry'))
        for sig, command in signals: loop.add_signal_handler(sig, commands.put_nowait, command)
        async def initialize(session):
            await workflow(session)
        async def report(value):
            print(json.dumps(value), file=sys.stderr, flush=True)
        try:
            await serve(args.profile, binaries, args.workspace, spec, credentials=credentials,
                on_prepared=initialize, initialize_only=True, on_status=report, commands=commands,
                launch_guard=(args.launch, launch) if launch else None)
        finally:
            for sig, _ in signals: loop.remove_signal_handler(sig)
        if workflow.result is None:
            raise RuntimeError('Migration did not complete; the original request is retained for recovery')
        result = dict(state=workflow.result['state'], operation=workflow.request['operation'],
                      data_root=workflow.result['root'])
        if not workflow.abort:
            result.update(backup=workflow.result['backup'], conversations=workflow.result['receipt']['conversations'])
        print(json.dumps(result), flush=True)
    asyncio.run(run())


if __name__ == '__main__':
    main()
