"""Run the General Team on a single-machine Fleet (``pantheon local``).

Starts the profile's Fleet (controller, broker, Runner), saves the owner's
composed setup as the controller's ``general-team`` deployment and waits for
the controller to report it ready. Stopping sets every App's intent to stopped
and waits until the controller has stopped them, then closes the Fleet. The
controller's reconciler starts, restarts and stops Apps; this process does not.
"""
import argparse
import asyncio
from contextlib import AsyncExitStack
import json
import signal
import sys

from pantheon.apps.dependency_assembly import AssemblyError

STATUS_INTERVAL = 1.0


async def _cancel(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _context(runtime, directory_root):
    info = runtime.coordinates
    return {'controller': info.controller, 'nats': info.nats, 'trust_roots_pem': info.ca_certificate.read_text(),
            'workspace': str(runtime.workspace), 'directory_root': str(directory_root)}


async def _deliver_secrets(deployment, names, *, owner_key, controller, directory, credentials):
    values = {ref.removeprefix('node-secret://'): credential for ref, credential in (credentials.entries if credentials else ())}
    for name in sorted(names):
        if name == 'owner-controller':
            await deployment.put_secret(name, owner_key, controller)
        elif name == 'directory':
            await deployment.put_secret(name, directory.key, directory.endpoint)
        elif name in values:
            await deployment.put_secret(name, values[name].key, values[name].endpoint)
        else:
            raise AssemblyError(f'The setup names credential {name!r}; supply it in the private credential file')


async def serve(*, profile, binaries, workspace, spec, release, credentials=None, commands, report,
                on_ready=None, recover=False):
    """Bring the deployment up, serve on_ready while it runs, stop on request."""
    import nats
    from pantheon.apps.resolver import AppInstanceResolver
    from pantheon.models.local_directory import LocalModelDirectory
    from .deployment import LocalDeployment, deployment_spec, release_archive, render
    from .directory import DirectoryServer
    from .fleet import LocalFleet, OWNER_KEY_PREFIX, _private_secret

    async with LocalFleet(profile, binaries, workspace=workspace, recover=recover) as runtime, \
            AsyncExitStack() as cleanup:
        info = runtime.coordinates
        key = _private_secret(runtime.root / 'owner.key', OWNER_KEY_PREFIX)
        deployment = LocalDeployment(runtime, owner_key=key, tls_context=info.tls_context())
        cleanup.push_async_callback(deployment.aclose)
        watcher = asyncio.create_task(runtime.wait())
        cleanup.push_async_callback(_cancel, watcher)

        async def stop():
            if await deployment.set_intent('stopped') is not None:
                await deployment.wait('stopped', report=report, interval=STATUS_INTERVAL)
            else:
                await report({'state': 'stopped', 'profile': str(runtime.root), 'apps': {}})
            runtime.recovery_drained = True

        if recover:
            await stop()
            return
        models = runtime.root / 'models'
        directory = LocalModelDirectory(models, owner=info.fleet_id)
        await directory.initialize()
        server = cleanup.enter_context(DirectoryServer(directory, state=runtime.root / 'directory.json'))
        apps, secrets = render(spec, _context(runtime, models))
        await _deliver_secrets(deployment, secrets, owner_key=key, controller=info.controller,
                               directory=server, credentials=credentials)
        url, sha256 = await asyncio.to_thread(release_archive, release, runtime.releases)
        await deployment.apply(deployment_spec(apps, url, sha256, secrets))
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        cleanup.push_async_callback(nc.close)
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(runtime.workspace),
                                       connection=nc)
        cleanup.push_async_callback(resolver.close)

        foreground = None
        stopping = False
        previous = None
        try:
            while not stopping:
                await deployment.get()
                summary = deployment.summary()
                if summary != previous:
                    await report(summary)
                    previous = summary
                if summary['state'] == 'ready' and on_ready is not None and foreground is None:
                    foreground = asyncio.create_task(on_ready(deployment, resolver, runtime))
                waiting = asyncio.create_task(commands.get())
                watched = [waiting, watcher] + ([foreground] if foreground is not None else [])
                done, _ = await asyncio.wait(watched, timeout=STATUS_INTERVAL, return_when=asyncio.FIRST_COMPLETED)
                if waiting not in done:
                    await _cancel(waiting)
                if watcher in done:
                    await watcher
                    raise RuntimeError('Local Fleet infrastructure exited')
                if foreground is not None and foreground in done:
                    stopping = True  # the foreground client finished: drain
                elif waiting in done and waiting.result() == 'stop':
                    stopping = True
        finally:
            if foreground is not None:
                await _cancel(foreground)
            await stop()


def _log_failure(profile):
    """Keep the failure's traceback in the profile (never on the status pipe)."""
    import time
    import traceback
    from pathlib import Path
    try:
        with (Path(profile) / 'launcher.log').open('a') as log:
            log.write(time.strftime('%Y-%m-%dT%H:%M:%S ') + traceback.format_exc() + '\n')
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=(
        'Ctrl-C or SIGTERM stops the Apps and waits for them before exiting. '
        'Use --agent for a terminal client or --desktop-agent for the native Desktop protocol.'))
    parser.add_argument('--launch', help='Private saved launch description (bundle, setup, profile, workspace, credentials)')
    parser.add_argument('--bundle', help='Packaged local Fleet and Agent App release set')
    parser.add_argument('--setup', help='Private Agent/model/tool setup, required with --bundle')
    parser.add_argument('--profile')
    parser.add_argument('--workspace')
    parser.add_argument('--credentials', help='Private credential JSON outside the workspace')
    parser.add_argument('--recover', action='store_true',
                        help='Take over surviving infrastructure after an owner crash and stop the Apps')
    client = parser.add_mutually_exclusive_group()
    client.add_argument('--agent', help='Agent App alias to open in the terminal; add -i for one-shot mode')
    client.add_argument('--desktop-agent', help='Agent alias for the native Desktop readiness/control protocol')
    parser.add_argument('-i', '--input', help='Send one prompt through the running Agent App, then stop')
    parser.add_argument('--stream', action='store_true', help='Emit JSON event/result lines for a one-shot prompt')
    selected = parser.add_mutually_exclusive_group()
    selected.add_argument('--chat-id')
    selected.add_argument('-r', '--resume', nargs='?', const=True, default=False)
    parser.add_argument('--template-json', help='Private JSON team template for a new conversation')
    parser.add_argument('--model', help='Explicit model selection for the first Agent in this conversation')
    args = parser.parse_args(argv)

    from pantheon.apps.local_agent import bundle_release, compose_profile, read_bundle
    from .credentials import read_credentials
    from .launch import private_json, read_launch

    if args.launch:
        if any((args.bundle, args.setup, args.profile, args.workspace, args.credentials)):
            parser.error('--launch supplies product, setup, profile, workspace and credentials; do not override them')
        launch = read_launch(args.launch)
        for key in ('bundle', 'setup', 'profile', 'workspace', 'credentials'):
            setattr(args, key, launch.get(key))
    if not (args.bundle and args.setup and args.profile and args.workspace):
        parser.error('--bundle, --setup, --profile and --workspace are required without --launch')
    if args.recover and args.agent is not None:
        parser.error('--recover only stops the Apps; start the Agent separately afterwards')
    if args.input is not None and (args.agent is None or not args.input.strip()):
        parser.error('--input requires --agent and a nonempty prompt')
    if args.stream and args.input is None:
        parser.error('--stream requires a one-shot --input prompt')
    if args.agent is None and (args.chat_id or args.resume is not False or args.template_json or args.model):
        parser.error('Conversation options require --agent')
    binaries, entries = read_bundle(args.bundle)
    spec = compose_profile(entries, private_json(args.setup))
    for alias in (args.agent, args.desktop_agent):
        if alias is not None and alias not in spec['apps']:
            parser.error('--agent/--desktop-agent must name an App of this setup')
    credentials = None
    if args.credentials:
        try:
            credentials = read_credentials(args.credentials, spec, args.workspace)
        except AssemblyError as exc:
            parser.error(str(exc))
    template = private_json(args.template_json) if args.template_json else None
    if template is not None and not isinstance(template, dict):
        parser.error('--template-json must contain a team template object')

    async def terminal(deployment, resolver, runtime):
        from pantheon.agent_client import AgentAppClient, run_once
        client = AgentAppClient(await deployment.bind_rpc(resolver, args.agent, 'agent'))
        options = dict(chat_id=args.chat_id, resume=args.resume, template=template, model=args.model)
        if args.input is None:
            from pantheon.agent_terminal import run_interactive
            await run_interactive(client, **options)
        elif args.stream:
            from pantheon.agent_terminal import run_json_stream
            await run_json_stream(client, args.input, **options)
        else:
            print(json.dumps(await run_once(client, args.input, **options)), flush=True)

    async def run():
        commands = asyncio.Queue()
        loop = asyncio.get_running_loop()
        signals = ((signal.SIGINT, 'stop'), (signal.SIGTERM, 'stop'))
        for sig, command in signals:
            loop.add_signal_handler(sig, commands.put_nowait, command)
        control = None
        on_ready = terminal if args.agent is not None else None

        async def report(value):
            try:
                print(json.dumps(value), file=sys.stderr if args.agent is not None else sys.stdout, flush=True)
            except (BrokenPipeError, OSError):
                pass  # a closed status pipe must not interrupt stopping the Apps
        try:
            if args.desktop_agent is not None:
                from .desktop import control_input, desktop_view, emit
                control = asyncio.create_task(control_input(commands))

                async def on_ready(deployment, resolver, runtime):
                    await desktop_view(deployment, resolver, spec, args.desktop_agent, runtime.root)

                async def report(value):
                    emit('status', value)
            try:
                await serve(profile=args.profile, binaries=binaries, workspace=args.workspace, spec=spec,
                            release=bundle_release(args.bundle), credentials=credentials, commands=commands,
                            report=report, on_ready=on_ready, recover=args.recover)
            except Exception as error:
                _log_failure(args.profile)
                await report({'state': 'stopped', 'needs_attention': True, 'profile': str(args.profile),
                              'error': str(error) if isinstance(error, AssemblyError) else
                              'Local Apps failed; inspect the profile logs and retry'})
                raise
        finally:
            if control is not None:
                await _cancel(control)
            for sig, _ in signals:
                loop.remove_signal_handler(sig)
    try:
        asyncio.run(run())
    except Exception:
        # Do not expose arbitrary App/transport exceptions or credential paths.
        print('Local profile failed. Inspect the profile status and logs before retrying.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
