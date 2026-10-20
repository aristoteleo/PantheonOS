"""Build the ordinary Web App without an embedded Agent or global tool bus."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile


def build(output: Path, platform: str):
    from pantheonos.appkit.portable import definition
    from pantheonos.appkit.schema import parse_manifest
    source = Path(__file__).resolve().parent
    from pantheonos.appkit.vendoring import add_package_inits, builtin_app_dir, vendor_file, vendor_tree
    runtime = source.parents[1] / 'pantheonos'
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.web-build-', dir=output.parent) as temp:
        package = Path(temp) / 'package'
        package.mkdir()
        manifest = json.loads((source / 'app.json').read_text())
        manifest.update(version='0.6.6', runtime='process', surface='headless',
                        execution={'protocol': 1, 'manifest': 'fleet.json'})
        manifest['entry'] = {'backend': 'backend/__init__.py'}
        manifest['provides']['tools'] = [tool for tool in manifest['provides']['tools']
                                         if tool['name'] in {'duckduckgo_search', 'web_crawl'}]
        for tool in manifest['provides']['tools']:
            for parameter in tool['params']:
                if parameter['name'] in {'query', 'urls'}:
                    parameter['required'] = True
                    parameter.pop('default', None)
                elif parameter['name'] == 'time_limit':
                    parameter['default'] = None
        manifest['provides']['interfaces'] = [
            {'name': 'web-search', 'version': 1, 'tools': ['duckduckgo_search']},
            {'name': 'web-crawl', 'version': 1, 'tools': ['web_crawl']},
        ]
        manifest['notes'] = 'Shared Web service with App-owned browser cache; no Agent or model dependency.'
        parse_manifest(manifest)
        (package / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
        vendor = package / 'backend/_vendor'
        for name in ('toolset.py', 'utils/log.py', 'utils/misc.py', 'appkit/toolset_backend.py',
                     'internal/package_runtime/context.py', 'remote/backend/base.py'):
            vendor_file(name, vendor)
        vendor_tree('funcdesc', vendor)
        for name in ('__init__.py', 'managed.py'):
            dest = builtin_app_dir(vendor) / 'web' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
        add_package_inits(vendor)
        (package / 'backend/__init__.py').write_text('from pantheonos.appkit.builtin.web.managed import register\n')
        (package / 'requirements.txt').write_text(
            'loguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\n'
            'ddgs==9.16.0\ncrawl4ai==0.8.0\nplaywright==1.58.0\n')
        (package / 'runtime-resources.json').write_text(json.dumps(
            {'protocol': 1, 'playwright': ['chromium']}, indent=2) + '\n')
        adapter = package / '.fleet-runtime'
        adapter.mkdir()
        shutil.copyfile(source.parent / 'desktop/app_runtime.py', adapter / 'app_runtime.py')
        for name in ('host.py', 'install.py', 'launch.py'):
            shutil.copyfile(runtime / 'appkit/portable_runtime' / name, adapter / name)
        execution = definition(manifest, platform)
        execution['hooks']['before_stop']['component'] = 'backend'
        (package / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
        (package / 'README.md').write_text(
            '# Web App\n\n'
            'Provides the original DDGS search and Crawl4AI browser-rendered Markdown tools. '
            'The service is shared; calls do not own persistent browser sessions. '
            'RPC admission and shutdown use the ordinary App host. '
            'Crawl state belongs to this App data directory. TLS verification is enabled.\n\n'
            'Installation prepares pinned Python dependencies and Playwright Chromium using '
            'runtime-resources.json. Browser binaries share the dependency environment and '
            'survive code-only updates. Launch uses that exact prepared browser directory. '
            'Linux nodes must already provide Chromium system libraries; no elevated package '
            'installation, desktop capture permission, model key or Agent is required.\n')
        package.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    build(args.output, args.platform)
