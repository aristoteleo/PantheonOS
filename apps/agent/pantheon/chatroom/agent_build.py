"""Build the Pantheon Agent package from its source repository.

This file is the repository's build.py (docs/agent/agent-app-repository.md §2). The
Desktop runs it in a copy of one commit with APP_BUILD_OUTPUT,
APP_BUILD_PLATFORM, APP_BUILD_REVISION and APP_BUILD_CACHE set. It uses the
standard library only.

Two inputs are prebuilt and pinned by SHA-256 in build.json: the Model
Services transport executable per platform, and the compiled Agent window
with the digest of the frontend/ source it was built from. When frontend/ has
that digest the prebuilt window is used; otherwise it is built with pnpm
(Node.js 20; pnpm runs through npx when it is not installed).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

PLATFORMS = {f'{os_name}-{arch}' for os_name in ('linux', 'darwin') for arch in ('amd64', 'arm64')}
# Never part of a source tree or its digest.
GENERATED = {'.git', '__pycache__', 'node_modules', '.venv', 'dist-agent-app'}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def source_files(root: Path):
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_file() and not set(relative.parts) & GENERATED:
            yield relative, path


def tree_digest(root: Path) -> str:
    """Digest of a source tree's paths and contents."""
    digest = hashlib.sha256()
    for relative, path in source_files(root):
        digest.update(f'{relative.as_posix()}\0{sha256_file(path)}\n'.encode())
    return digest.hexdigest()


def frontend_archive(directory: Path) -> bytes:
    """A deterministic archive of a compiled Agent window."""
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(p for p in directory.rglob('*') if p.is_file()):
            info = tarfile.TarInfo(path.relative_to(directory).as_posix())
            info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 0
            with path.open('rb') as stream:
                archive.addfile(info, stream)
    return out.getvalue()


def fetch(ref: dict, cache: Path) -> Path:
    """A pinned input from the cache, else downloaded and verified."""
    expected = ref['sha256']
    path = cache / 'inputs' / expected
    if path.is_file() and sha256_file(path) == expected:
        return path
    if not ref.get('url'):
        raise SystemExit(f'Build input {expected[:12]} is not available')
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix('.partial')
    print(f'Downloading {ref["url"]}', flush=True)
    with urllib.request.urlopen(ref['url'], timeout=120) as response, partial.open('wb') as stream:
        shutil.copyfileobj(response, stream)
    if sha256_file(partial) != expected:
        partial.unlink()
        raise SystemExit(f'Build input {ref["url"]} does not match its SHA-256')
    partial.replace(path)
    return path


def copy_tree(source: Path, target: Path, *, skip_env: bool = False) -> None:
    for relative, path in source_files(source):
        if skip_env and any(part.startswith('.env') for part in relative.parts):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def unpack(archive: Path, target: Path) -> None:
    target.mkdir(parents=True)
    with tarfile.open(archive) as members:
        for member in members.getmembers():
            if not member.isfile() or member.name.startswith('/') or '..' in Path(member.name).parts:
                raise SystemExit(f'Unexpected entry in the prebuilt frontend: {member.name}')
        members.extractall(target, filter='data')


def run(argv: list[str], cwd: Path, env: dict) -> None:
    print('$ ' + ' '.join(argv), flush=True)
    result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise SystemExit(f'{argv[0]} failed with exit {result.returncode}')


def build_frontend(source: Path, target: Path, *, version: str, revision: str, cache: Path, pinned: dict,
                   tools: dict) -> None:
    """The prebuilt window when frontend/ is the source it was built from, else pnpm."""
    if not source.is_dir() or tree_digest(source) == pinned.get('source'):
        unpack(fetch(pinned, cache), target)
        return
    # The window's lockfile decides its package manager (pnpm, pinned in build.json).
    pnpm = shutil.which('pnpm')
    npx = shutil.which('npx')
    if pnpm is None and npx is None:
        raise SystemExit('The Agent window changed; building it needs Node.js 20 and npm on this node')
    manager = [pnpm] if pnpm else [npx, '--yes', 'pnpm@' + tools.get('pnpm', '10')]
    # node_modules is kept between builds; everything else is this commit's.
    work = cache / 'frontend'
    work.mkdir(parents=True, exist_ok=True)
    for child in work.iterdir():
        if child.name != 'node_modules':
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    copy_tree(source, work)
    env = {**os.environ, 'AGENT_APP_VERSION': version, 'AGENT_APP_REVISION': revision[:12],
           'AGENT_APP_BUILD_DIR': str(target), 'CI': '1'}
    lock = sha256_file(work / 'pnpm-lock.yaml')
    marker = work / 'node_modules' / '.pnpm-lock.sha256'
    if not marker.is_file() or marker.read_text() != lock:
        run([*manager, 'install', '--frozen-lockfile', '--store-dir', str(cache / 'pnpm-store')], work, env)
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(lock)
    run([*manager, 'run', 'build:agent-app'], work, env)
    for name in ('index.js', 'agent.css', 'build-report.json'):
        if not (target / name).is_file():
            raise SystemExit(f'The frontend build produced no {name}')


def build(source: Path, output: Path, *, platform: str, revision: str, cache: Path) -> dict:
    """Assemble the runnable package of this source tree into output."""
    if platform not in PLATFORMS:
        raise SystemExit(f'The Agent runs on Linux or macOS nodes, not {platform}')
    manifest = json.loads((source / 'app.json').read_text())
    config = json.loads((source / 'build.json').read_text())
    if config.get('protocol') != 1:
        raise SystemExit('Unsupported build.json')
    version = manifest['version']
    vendor = output / 'backend' / '_vendor' / 'pantheon'
    copy_tree(source / 'backend' / 'pantheon', vendor, skip_env=True)
    moved = (vendor / '_moved.py').is_file()
    (vendor / '__init__.py').write_text(f'"""Pantheon Agent release runtime."""\n__version__ = {version!r}\n'
                                        + ('from ._moved import install\ninstall()\n' if moved else ''))
    # The platform package it runs on, in a source exported since the split
    # (an older source carries everything in its pantheon package).
    platform_source = source / 'backend' / 'pantheonos'
    if platform_source.is_dir():
        copy_tree(platform_source, vendor.parent / 'pantheonos', skip_env=True)
    models = (vendor.parent / 'pantheonos' if platform_source.is_dir() else vendor) / 'models'
    shutil.copyfile(source / 'backend' / '__init__.py', output / 'backend' / '__init__.py')
    transport = config['inputs']['transport'].get(platform)
    if transport is None:
        raise SystemExit(f'build.json pins no Model Services transport for {platform}')
    executable = models / 'fleet-app-transport'
    shutil.copyfile(fetch(transport, cache), executable)
    executable.chmod(0o755)
    for name in ('cli.py', 'requirements.txt'):
        shutil.copyfile(source / name, output / name)
    if (source / 'terminal.py').is_file():
        # The terminal chat (docs/os/pantheon-cli.md); a source older than it has none.
        shutil.copyfile(source / 'terminal.py', output / 'terminal.py')
    copy_tree(source / 'fleet-runtime', output / '.fleet-runtime')
    build_frontend(source / 'frontend', output / 'frontend', version=version, revision=revision,
                   cache=cache, pinned=config['inputs']['frontend'], tools=config.get('tools', {}))
    package = {}
    for key, value in manifest.items():
        if key == 'build':
            continue
        package[key] = value
        if key == 'entry':
            package['execution'] = {'protocol': 1, 'manifest': 'fleet.json', 'rpc_transport': 'fleet',
                                    'release_inventory': 'release.json'}
    (output / 'app.json').write_text(json.dumps(package, indent=2) + '\n')
    execution = json.loads((source / 'execution.json').read_text())
    os_name, arch = platform.split('-')
    execution.update(app_id=manifest['id'], version=version)
    execution['requires'].update(os=[os_name], arch=[arch])
    if 'dataSchema' in manifest:
        execution['data_schema'] = manifest['dataSchema']
    (output / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
    # Content inventory binds frontend, backend, host and helper in one
    # immutable package; no source-machine paths or timestamps are saved.
    inventory = {p.relative_to(output).as_posix(): sha256_file(p) for p in sorted(output.rglob('*')) if p.is_file()}
    (output / 'release.json').write_text(json.dumps({'protocol': 1, 'platform': platform,
        'version': version, 'files': inventory}, indent=2) + '\n')
    return package


def main() -> None:
    output = Path(os.environ['APP_BUILD_OUTPUT'])
    build(Path.cwd(), output, platform=os.environ['APP_BUILD_PLATFORM'],
          revision=os.environ.get('APP_BUILD_REVISION', ''), cache=Path(os.environ['APP_BUILD_CACHE']))
    print(f'Built Pantheon Agent for {os.environ["APP_BUILD_PLATFORM"]}', flush=True)


if __name__ == '__main__':
    main()
