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
                  transport, model_aliases, go='go', catalog=()):
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
    from pantheon.platform.dependency_package import build_package as allocator
    from pantheon.platform.model_dependency_package import build_package as access

    builders = {'desktop': desktop, 'evolution': evolution, 'fleet': fleet,
                'web': web, 'model-management': management, 'allocator': allocator,
                'model-access': access, 'files-models': access}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.general-agent-release-', dir=destination.parent) as temporary:
        root = Path(temporary) / 'release'
        root.mkdir()
        for alias, builder in builders.items():
            builder(root / alias, platform)
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
            if alias == 'files':
                dependency['binding'] = 'startup'
            dependencies[app_id] = dependency
        agent(root / 'agent', platform, version=version, frontend=frontend,
              transport=transport, dependencies=dependencies)
        index_packages(root, {alias: {platform: root / alias} for alias in sorted(reserved | set(model_aliases))})
        if 'connector' in model_aliases:
            # The deployment profile the Fleet controller sets an owner's Agent up from.
            from .release_profile import write_profile
            from pantheon.platform.first_run import DEFAULT_TIERS
            write_profile(root, platform, tiers=DEFAULT_TIERS, catalog=catalog)
        # No partial output is visible after a failed builder or index check.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        root.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--frontend', required=True, type=Path, help='Paired build:agent-app output')
    parser.add_argument('--notebook-frontend', required=True, type=Path, help='Complete Notebook frontend build')
    parser.add_argument('--transport', required=True, type=Path, help='Target fleet-app-transport executable')
    parser.add_argument('--model-app', action='append', default=[], dest='model_aliases')
    parser.add_argument('--go', default='go', help='Build-host Go executable for the native Shell App')
    parser.add_argument('--no-catalog', action='store_true',
                        help='Publish only the tier models on the platform Connector (no OpenRouter catalog fetch)')
    options = vars(parser.parse_args())
    if not options.pop('no_catalog') and 'connector' in options['model_aliases']:
        import asyncio
        from .release_profile import platform_catalog
        options['catalog'] = asyncio.run(platform_catalog())
    options['destination'] = options.pop('output')
    print(build_release(**options))


if __name__ == '__main__':
    main()
