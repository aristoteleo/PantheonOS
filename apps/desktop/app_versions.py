"""Per-App Git history and immutable launch trees, owned by the user.

Defaults and snapshots live outside editable App directories. A default change
never checks out, rewrites, or kills the code used by an existing window.
"""
from __future__ import annotations

import json
import re
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path

from pantheon.apps.store_release import git, validate_manifest, MAX_TREE_BYTES
from pantheon.apps.versioning import fork


class AppVersions:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.records

    def repository(self, directory: Path, scope: str, app_id: str) -> Path:
        if (directory / '.git').exists():
            return directory
        return self.root / 'repositories' / scope / app_id

    def ensure(self) -> dict:
        """Migrate legacy installs once; keep official files read-only."""
        with self.manager.lock():
            warnings = []
            for app in self.manager.inventory()['apps']:
                source = Path(app['dir'])
                repo = self.repository(source, app['scope'], app['id'])
                try:
                    if not (repo / '.git').exists():
                        if app['scope'] == 'builtin':
                            repo.parent.mkdir(parents=True, exist_ok=True)
                            with tempfile.TemporaryDirectory(dir=repo.parent) as temp:
                                stage = fork(source, Path(temp) / 'app')
                                stage.rename(repo)
                        else:
                            # Initial snapshot of this user's current files, with no
                            # checkout/reset and no changes to a surrounding monorepo.
                            git(source, 'init', '-q')
                            exclude = source / '.git/info/exclude'
                            with exclude.open('a') as rules:
                                rules.write('\nnode_modules/\n__pycache__/\n*.pyc\n.venv/\n.env\n.env.*\n')
                            git(source, 'add', '-A')
                            git(source, '-c', 'user.name=Pantheon Store', '-c', 'user.email=store@pantheon',
                                'commit', '-qm', f"Import {app['id']} {app['manifest']['version']}")
                            git(source, 'tag', f"v{app['manifest']['version']}")
                    elif app['scope'] == 'builtin' and repo != source:
                        tag = f"v{app['manifest']['version']}"
                        if not git(repo, 'tag', '--list', tag).strip():
                            # New official versions extend the same graph. Never
                            # move an existing version tag or import runtime caches.
                            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                                stage = fork(source, Path(temp) / 'app')
                                shutil.rmtree(stage / '.git')
                                shutil.copytree(repo / '.git', stage / '.git')
                                git(stage, 'add', '-A')
                                git(stage, '-c', 'user.name=Pantheon Store', '-c', 'user.email=store@pantheon',
                                    'commit', '--allow-empty', '-qm', f'Update official {app["id"]} to {tag}')
                                git(stage, 'tag', tag)
                                # Fetch into the retained repository before changing
                                # its checked-out version; snapshots have separate trees.
                                git(repo, 'fetch', '-q', str(stage), f'refs/tags/{tag}:refs/tags/{tag}')
                                git(repo, 'checkout', '-q', '--detach', tag)
                except (ValueError, OSError) as exc:
                    warnings.append(f"{app['id']}: {exc}")
            return {'success': True, 'warnings': warnings}

    def _default_path(self, app_id: str) -> Path:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', app_id):
            raise ValueError('Invalid App id')
        return self.root / 'defaults' / f'{app_id}.json'

    def default(self, app_id: str) -> dict | None:
        path = self._default_path(app_id)
        return json.loads(path.read_text()) if path.exists() else None

    @staticmethod
    def restriction(manifest: dict) -> str:
        entry = manifest.get('entry') or {}
        if not entry:
            return 'This App uses a node-managed runtime. Its runtime version is managed by Fleet.'
        if (entry.get('frontend') or '').startswith('ui:'):
            return 'This App runs in the Desktop shell and updates with the Desktop.'
        if ':' in (entry.get('backend') or '') or entry.get('nativeDriver') or entry.get('frontendType') == 'native-stream':
            return 'This App uses a node-managed runtime. Its runtime version is managed by Fleet.'
        return ''

    def versions(self, app_id: str, scope: str, repository_id: str = '') -> dict:
        app = self.manager.find(app_id, scope, repository_id)
        repo = self.repository(Path(app['dir']), scope, app_id)
        items = []
        if (repo / '.git').exists():
            for tag in git(repo, 'tag', '--list', '--sort=-version:refname').splitlines():
                try:
                    commit = git(repo, 'rev-parse', '--verify', '--end-of-options', f'refs/tags/{tag}^{{commit}}').strip()
                    manifest = self._manifest(repo, commit)
                    if manifest['id'] != app_id:
                        continue
                    items.append({'tag': tag, 'commit': commit, 'version': manifest['version'],
                                  'restriction': self.restriction(manifest)})
                except ValueError:
                    continue
        return {'success': True, 'versions': items, 'default': self.default(app_id),
                'restriction': self.restriction(app['manifest'])}

    @staticmethod
    def _manifest(repo: Path, commit: str) -> dict:
        for name in ('app.json', 'atrium.json'):
            try:
                manifest = json.loads(git(repo, 'show', f'{commit}:{name}'))
                if manifest.get('entry'):
                    return validate_manifest(manifest)
                if manifest.get('id') and manifest.get('version'):
                    return manifest
            except ValueError:
                continue
        raise ValueError('Version has no valid App manifest')

    def resolve(self, app_id: str, scope: str = '', version: str = '', repository_id: str = '') -> dict:
        """Resolve only a tag or full SHA; never execute code while resolving."""
        self._default_path(app_id)  # validate identity before constructing paths
        selected = self.default(app_id) if not scope and not version and not repository_id else None
        repository_id = repository_id or (selected or {}).get('repository_id', '')
        if repository_id:
            repository_id = str(uuid.UUID(repository_id))
        scope = scope or (selected or {}).get('scope', '')
        version = version or (selected or {}).get('commit', '')
        if scope and scope not in ('builtin', 'workspace', 'user', 'fork'):
            raise ValueError('Invalid App scope')
        # Existing windows remain restorable even if the source was upgraded.
        if scope and re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', version):
            destination = self.root / 'snapshots' / scope / (repository_id or app_id) / version
            if destination.is_dir():
                manifest = self._read_manifest(destination)
                return self._resolved(app_id, scope, version, manifest, destination, repository_id)
        app = self.manager.find(app_id, scope or None, repository_id)
        repository_id = app['repository_id']
        scope = app['scope']
        repo = self.repository(Path(app['dir']), scope, app_id)
        if not (repo / '.git').exists():
            self.ensure()
            repo = self.repository(Path(app["dir"]), scope, app_id)
        if not version:
            version = f"v{app['manifest']['version']}"
        ref = version if re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', version) else f'refs/tags/{version}'
        commit = git(repo, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').strip()
        manifest = self._manifest(repo, commit)
        reason = self.restriction(manifest)
        if reason:
            raise ValueError(reason)
        if manifest['id'] != app_id:
            raise ValueError('Version belongs to another App')
        destination = self.root / 'snapshots' / scope / repository_id / commit
        with self.manager.lock():
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
                    archive = Path(temp) / 'tree.tar'
                    git(repo, 'archive', '--format=tar', f'--output={archive}', commit)
                    stage = Path(temp) / 'app'
                    stage.mkdir()
                    with tarfile.open(archive) as tree:
                        members = tree.getmembers()
                        if sum(m.size for m in members) > MAX_TREE_BYTES:
                            raise ValueError('App version exceeds the snapshot size limit')
                        for member in members:
                            target = stage / member.name
                            if not target.resolve().is_relative_to(stage.resolve()) or not (member.isdir() or member.isfile()) or '.git' in Path(member.name).parts:
                                raise ValueError(f'Unsupported App file: {member.name}')
                        tree.extractall(stage, members=members, filter='data')
                    stage.rename(destination)
        return self._resolved(app_id, scope, commit, manifest, destination, repository_id)

    @staticmethod
    def _read_manifest(root: Path) -> dict:
        return validate_manifest(json.loads(next(root / n for n in ('app.json', 'atrium.json') if (root / n).is_file()).read_text()))

    def _resolved(self, app_id, scope, commit, manifest, destination, repository_id=''):
        if manifest['id'] != app_id or self.restriction(manifest):
            raise ValueError('This version cannot run independently')
        return {'success': True, 'manifest': manifest, 'dir': str(destination), 'scope': scope,
                'repository_id': repository_id, 'revision': {'scope': scope, 'commit': commit, 'version': manifest['version'], 'repository_id': repository_id}}

    def set_default(self, app_id: str, scope: str, version: str, repository_id: str = '') -> dict:
        resolved = self.resolve(app_id, scope, version, repository_id)
        path = self._default_path(app_id)
        with self.manager.lock():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(resolved['revision']))
            temp.replace(path)
        return {'success': True, 'default': resolved['revision']}
