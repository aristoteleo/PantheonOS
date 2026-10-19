"""Build the complete General Team release from existing ordinary App builders.

This is a source-distribution build command, not a runtime launcher. It creates
the existing release-set format; owner settings, credentials, model selection
and Fleet executable delivery remain separate inputs to the local product.
"""
import argparse
import json
from pathlib import Path
import tempfile

from .dependency_assembly import AssemblyError, NAME, _matches
from .general_agent_preset import PROVIDERS
from .release_set import index_packages
from .tool_profiles import compile_tool_profile


def build_release(destination, platform, *, version, frontend, notebook_frontend,
                  transport, model_aliases, go='go', catalog=(), frontend_source=None,
                  input_urls=None, agent_source=None):
    """Assemble exact provider contracts and publish only a complete release.

    Each model alias packages the original Connector. Model engines/endpoints
    are supplied later by the owner, not selected or started during a build.
    At most four aliases fit alongside the twelve mandatory product packages
    in the existing sixteen-App release index.
    """
    if platform not in {f'{os}-{arch}' for os in ('linux', 'darwin') for arch in ('amd64', 'arm64')}:
        raise AssemblyError('The complete Agent product requires a POSIX target')
    reserved = {'agent', 'allocator', 'model-access', 'files-models', *PROVIDERS}
    if (not isinstance(model_aliases, (list, tuple)) or len(model_aliases) > 4
            or any(not _matches(NAME, alias) or alias in reserved for alias in model_aliases)
            or len(set(model_aliases)) != len(model_aliases)):
        raise AssemblyError('Supply up to four unique model App aliases without product alias collisions')
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)

    # Builder imports belong to the source build, never to the local launcher or
    # platform runtime. Individual builders retain their own byte/input checks.
    from pantheon.apps.builtin.desktop.build_managed import build as desktop
    from pantheon.apps.builtin.evolution.build_managed import build as evolution
    from pantheon.apps.builtin.file.build_managed import build as files
    from pantheon.apps.builtin.fleet.build_managed import build as fleet
    from pantheon.apps.builtin.notebook.build_managed import build as notebook
    from pantheon.apps.builtin.shell.build_managed import build as shell
    from pantheon.apps.builtin.web.build_managed import build as web
    from pantheon.chatroom.package import build_package as agent
    from pantheon.models.connector_package import build_package as connector
    from pantheon.models.management_package import build_package as management
    from pantheon.apps.allocator_package import build_package as allocator
    from pantheon.models.access_package import build_package as access

    # The Desktop declares its data port: a Fleet deployment serves it through
    # the App gateway (data mode 'gateway'); a single machine uses loopback.
    builders = {'desktop': lambda out, platform: desktop(out, platform, data_mode='gateway'),
                'evolution': evolution, 'fleet': fleet,
                'web': web, 'model-management': management, 'allocator': allocator,
                'model-access': access, 'files-models': access}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.general-agent-release-', dir=destination.parent) as temporary:
        root = Path(temporary) / 'release'
        root.mkdir()
        for alias, builder in builders.items():
            builder(root / alias, platform)
        # The controller hosts these (docs/fleet-orchestration.md §15) with the
        # manifests it embeds; the built contracts must be exactly those.
        hosted = Path(__file__).resolve().parents[2] / 'os' / 'internal' / 'hosted' / 'manifests'
        for alias, service in (('fleet', 'fleet'), ('allocator', 'dependency-binding'),
                               ('model-access', 'model-services-control'), ('files-models', 'model-services-control')):
            if json.loads((root / alias / 'app.json').read_text()) != json.loads((hosted / (service + '.json')).read_text()):
                raise AssemblyError(f'{alias}: the controller-hosted manifest differs; copy {alias}/app.json to os/internal/hosted/manifests/{service}.json')
        files(root / 'files', platform, model_sampling=True, image_generation=True, transport=transport)
        notebook(root / 'notebook', platform, frontend=Path(notebook_frontend))
        os_name, arch = platform.split('-')
        shell(root / 'shell', os_name, arch, go)
        for alias in model_aliases:
            connector(root / alias, platform)
        dependencies = {}
        for alias, (app_id, _) in PROVIDERS.items():
            manifest = json.loads((root / alias / 'app.json').read_text())
            if manifest['id'] != app_id:
                raise AssemblyError(f'Unexpected provider identity for {alias}')
            uses = [f"{item['name']}@{item.get('version', 1)}"
                    for item in manifest['provides']['interfaces']]
            options = {}
            if alias == 'shell':
                options['resource'] = {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}
            if alias == 'files':
                options['service_methods'] = ['stat_path']
            _, _, dependency = compile_tool_profile(manifest, alias=alias, uses=uses, **options)
            dependencies[app_id] = dependency
        # The Agent's own pantheon_command, which the commands its agents run
        # reach it by (docs/pantheon-cli.md): the allocator issues that grant
        # only for a dependency the consumer declares.
        dependencies['agent'] = {'range': version, 'uses': ['pantheon-command@1'], 'binding': 'runtime'}
        # The Agent is built from its exported source repository
        # (docs/agent-app-repository.md §2); agent_source keeps that tree.
        agent(root / 'agent', platform, version=version, frontend=frontend,
              transport=transport, dependencies=dependencies, frontend_source=frontend_source,
              urls=input_urls, source_output=agent_source)
        index_packages(root, {alias: {platform: root / alias} for alias in sorted(reserved | set(model_aliases))})
        if 'connector' in model_aliases:
            # The deployment profile the Fleet controller sets an owner's Agent up from.
            from .release_profile import write_profile
            from pantheon.apps.general_team import DEFAULT_TIERS
            write_profile(root, platform, tiers=DEFAULT_TIERS, catalog=catalog)
            # The controller-hosted model management builds from this source.
            write_connector_source(root / 'model-connector.tar')
        # No partial output is visible after a failed builder or index check.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        root.rename(destination)
    return destination


def write_connector_source(path):
    """The model connector source plus the group network sources (group/*).

    Deterministic: sorted entries, no ownership or timestamps.
    """
    import io
    import tarfile
    from pantheon.apps.registry import BUILTIN_ROOT
    connector = Path(BUILTIN_ROOT) / 'model-service'
    models = Path(__file__).resolve().parents[1] / 'models'
    files = {}
    for source in connector.rglob('*'):
        relative = source.relative_to(connector)
        if any(part in {'__pycache__', '.git'} for part in relative.parts) or source.suffix == '.pyc':
            continue
        if source.is_symlink():
            raise AssemblyError('Connector sources must not contain links')
        if source.is_file():
            files[relative.as_posix()] = source
    for name in ('group_network.py', 'group_mesh.py', 'group_inference.py'):
        files['group/' + name] = models / name
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            data = files[name].read_bytes()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o500 if files[name].stat().st_mode & 0o100 else 0o400
            archive.addfile(entry, io.BytesIO(data))


def build_window(ui_root, destination, *, version, env):
    """Export the Agent window's source and build it there, so the prebuilt
    window is exactly the source the Agent repository records."""
    import os
    import subprocess
    from pantheon.chatroom.package import export_frontend
    ui_root, destination = Path(ui_root).resolve(), Path(destination)
    source = export_frontend(ui_root, destination / 'frontend', env)
    revision = subprocess.run(['git', '-C', str(ui_root), 'rev-parse', '--short=12', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    (source / 'node_modules').symlink_to(ui_root / 'node_modules')
    try:
        subprocess.run(['npm', 'run', 'build:agent-app'], cwd=source, check=True,
            env={**os.environ, 'AGENT_APP_VERSION': version, 'AGENT_APP_REVISION': revision,
                 'AGENT_APP_BUILD_DIR': str(destination / 'dist')})
    finally:
        (source / 'node_modules').unlink()
    return source, destination / 'dist'


def write_inputs(assets, platform, frontend, transport, url_base):
    """The pinned prebuilt inputs, named for publishing next to the release set."""
    import shutil
    from pantheon.chatroom.agent_build import frontend_archive
    assets = Path(assets)
    assets.mkdir(parents=True, exist_ok=True)
    (assets / 'agent-frontend.tar.gz').write_bytes(frontend_archive(Path(frontend)))
    shutil.copyfile(transport, assets / f'fleet-app-transport-{platform}')
    base = url_base.rstrip('/') + '/'
    return {'frontend': base + 'agent-frontend.tar.gz', 'transport': base + f'fleet-app-transport-{platform}'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--frontend', type=Path, help='Paired build:agent-app output')
    parser.add_argument('--frontend-source', type=Path,
                        help='UI repository: export the Agent window source and build it (instead of --frontend)')
    parser.add_argument('--frontend-env', type=Path, help='VITE_* build configuration (KEY=VALUE lines)')
    parser.add_argument('--agent-source', type=Path, help='Also write the Agent source repository tree here')
    parser.add_argument('--assets', type=Path, help='Write the pinned prebuilt inputs here for publishing')
    parser.add_argument('--input-url-base', help='Where the pinned inputs will be published')
    parser.add_argument('--notebook-frontend', required=True, type=Path, help='Complete Notebook frontend build')
    parser.add_argument('--transport', required=True, type=Path, help='Target fleet-app-transport executable')
    parser.add_argument('--model-app', action='append', default=[], dest='model_aliases')
    parser.add_argument('--go', default='go', help='Build-host Go executable for the native Shell App')
    parser.add_argument('--no-catalog', action='store_true',
                        help='Publish only the tier models on the platform Connector (no OpenRouter catalog fetch)')
    options = vars(parser.parse_args())
    ui, env_file = options.pop('frontend_source'), options.pop('frontend_env')
    assets, url_base = options.pop('assets'), options.pop('input_url_base')
    if (options['frontend'] is None) == (ui is None):
        parser.error('Supply --frontend or --frontend-source')
    if (assets is None) != (url_base is None):
        parser.error('--assets and --input-url-base go together')
    import tempfile
    work = tempfile.TemporaryDirectory(prefix='agent-window-')
    if ui is not None:
        env = {}
        for line in (env_file.read_text().splitlines() if env_file else []):
            if line.strip() and not line.startswith('#'):
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip()
        options['frontend_source'], options['frontend'] = build_window(ui, work.name, version=options['version'], env=env)
    if assets is not None:
        options['input_urls'] = write_inputs(assets, options['platform'], options['frontend'], options['transport'], url_base)
    if not options.pop('no_catalog') and 'connector' in options['model_aliases']:
        import asyncio
        from .release_profile import platform_catalog
        options['catalog'] = asyncio.run(platform_catalog())
    options['destination'] = options.pop('output')
    print(build_release(**options))


if __name__ == '__main__':
    main()
