"""Personal Git forks and recoverable removal, outside live App discovery roots."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pantheon.apps.store_release import git


class AppBranches:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.records / 'forks'
        self.trash = manager.records / 'trash'

    def fork_download(self, download: dict, name: str = '') -> dict:
        from pantheon.apps.store_release import unpack_release
        upstream = download.get('repository') or {}
        if upstream.get('visibility') != 'public':
            raise ValueError('Fork a public Store repository')
        with self.manager.lock():
            repo_id = str(uuid.uuid4())
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / repo_id
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                stage = Path(temp) / 'app'
                manifest = unpack_release(download['app_release'], stage, download['version'])
                if upstream.get('app_id') != manifest['id'] or not upstream.get('id'):
                    raise ValueError('Release belongs to a different upstream repository')
                git(stage, 'switch', '-c', 'my-work')
                upstream = {**upstream, 'version': download['version'], 'commit': download['app_release']['commit']}
                if upstream.get('clone_url'):
                    git(stage, 'remote', 'add', 'upstream', upstream['clone_url'])
                    git(stage, 'update-ref', 'refs/remotes/upstream/main', upstream['commit'])
                    git(stage, 'config', 'branch.my-work.remote', 'upstream')
                    git(stage, 'config', 'branch.my-work.merge', 'refs/heads/main')
                stage.rename(target)
                self.manager._save(target.name, {'origin': 'fork', 'repository_id': repo_id,
                    'label': name or f"Fork of {upstream.get('name', manifest['name'])}",
                    'upstream': upstream, 'parent_repository_id': upstream['id'],
                    'parent_commit': upstream['commit'], 'installed_commit': upstream['commit']}, 'fork')
            return {'success': True, 'app_id': manifest['id'], 'scope': 'fork', 'repository_id': repo_id,
                    'directory': str(target), 'commit': upstream['commit']}

    def fork_app(self, app_id: str, scope: str, version: str = '', repository_id: str = '', name: str = '') -> dict:
        self.manager.versions.ensure()
        with self.manager.lock():
            app = self.manager.find(app_id, scope, repository_id)
            existing = next((a for a in self.manager.inventory()['apps']
                             if a['id'] == app_id and ((a['scope'] == 'fork' and
                             (a.get('install') or {}).get('parent_repository_id') == app['repository_id']) or
                             (a['scope'] == 'user' and (a.get('install') or {}).get('origin') in ('builtin', 'workspace')))), None)
            if existing and not name and not version:
                return {'success': True, 'app_id': app_id, 'scope': existing['scope'], 'existing': True, 'repository_id': existing['repository_id']}
            source = self.manager.versions.repository(Path(app['dir']), scope, app_id)
            ref = version or app['git']['commit']
            if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', ref):
                ref = f'refs/tags/{ref}'
            commit = git(source, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').strip()
            if self.manager.versions._manifest(source, commit)['id'] != app_id:
                raise ValueError('Version belongs to another App')
            self.root.mkdir(parents=True, exist_ok=True)
            new_id = str(uuid.uuid4())
            target = self.root / app_id
            if target.exists():
                target = self.root / new_id
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                stage = Path(temp) / 'app'
                # Clone the real history. No new root commit or copied version tag.
                git(source, 'clone', '-q', '--no-local', '--', str(source), str(stage))
                git(stage, 'checkout', '-q', '-B', 'my-work', commit)
                git(stage, 'remote', 'rename', 'origin', 'upstream')
                upstream = (app.get('install') or {}).get('published') or {}
                if upstream.get('id'):
                    upstream = {**upstream, 'commit': commit,
                                'version': self.manager.versions._manifest(source, commit)['version']}
                    if upstream.get('clone_url'):
                        git(stage, 'remote', 'set-url', 'upstream', upstream['clone_url'])
                        git(stage, 'update-ref', 'refs/remotes/upstream/main', commit)
                        git(stage, 'config', 'branch.my-work.remote', 'upstream')
                        git(stage, 'config', 'branch.my-work.merge', 'refs/heads/main')
                record = {'origin': 'fork', 'repository_id': new_id, 'parent_repository_id': app['repository_id'],
                          'upstream': upstream or None, 'label': name or 'My fork', 'parent_scope': scope, 'parent_commit': commit,
                          'installed_commit': commit, 'installed_at': datetime.now(timezone.utc).isoformat()}
                stage.rename(target)
                try:
                    self.manager._save(target.name, record, 'fork')
                except Exception:
                    target.rename(stage)
                    raise
            return {'success': True, 'app_id': app_id, 'scope': 'fork', 'commit': commit, 'repository_id': new_id, 'directory': str(target)}

    def remove(self, app_id: str, scope: str, repository_id: str = '') -> dict:
        if scope not in ('user', 'fork', 'workspace'):
            raise ValueError('Only user installations, workspace Apps and personal branches can be removed')
        with self.manager.lock():
            app = self.manager.find(app_id, scope, repository_id)
            source = Path(app['dir'])
            roots = ([self.root] if scope == 'fork' else
                     [root for root, kind in self.manager.roots if kind == 'workspace'] if scope == 'workspace' else
                     [self.manager.user_root, self.manager.records / 'installed'])
            if source.is_symlink() or source.resolve().parent not in [root.resolve() for root in roots]:
                raise ValueError('App directory is outside its managed root')
            archive_id = uuid.uuid4().hex
            archive = self.trash / archive_id
            archive.mkdir(parents=True)
            info = {'archive_id': archive_id, 'app_id': app_id, 'name': app['manifest']['name'],
                    'version': app['manifest']['version'], 'scope': scope,
                    'repository_id': app['repository_id'], 'directory_name': source.name, 'source_dir': str(source),
                    'removed_at': datetime.now(timezone.utc).isoformat(),
                    'record': app.get('install') or {}, 'modified': app['git'].get('modified', False)}
            (archive / 'record.json').write_text(json.dumps(info, indent=2))
            source.rename(archive / 'app')
            # The entire working tree AND Git branches survive, including dirty files.
            self.manager._record_path(source.name, scope).unlink(missing_ok=True)
            if (self.manager.versions.default(app_id) or {}).get('repository_id') == app['repository_id'] or (not (self.manager.versions.default(app_id) or {}).get('repository_id') and (self.manager.versions.default(app_id) or {}).get('scope') == scope):
                self.manager.versions._default_path(app_id).unlink(missing_ok=True)
            return {'success': True, 'archive_id': archive_id}

    def list_trash(self) -> dict:
        items = []
        if self.trash.exists():
            for directory in self.trash.iterdir():
                try:
                    if directory.is_symlink() or not (directory / 'app').is_dir():
                        continue
                    info = self.trash_entry(directory.name)
                    items.append({key: info[key] for key in ('archive_id', 'app_id', 'name', 'version', 'removed_at', 'modified')})
                except (OSError, ValueError, KeyError):
                    continue
        return {'success': True, 'items': sorted(items, key=lambda item: item['removed_at'], reverse=True)}

    def trash_entry(self, archive_id: str) -> dict:
        if not re.fullmatch(r'[a-f0-9]{32}', archive_id):
            raise ValueError('Invalid App trash entry')
        archive = self.trash / archive_id
        if self.trash.is_symlink() or archive.is_symlink() or not archive.is_dir() or (archive / 'app').is_symlink() or (archive / 'record.json').is_symlink():
            raise ValueError('Invalid App trash entry')
        info = json.loads((archive / 'record.json').read_text())
        if info.get('archive_id') != archive_id or info.get('scope') not in ('user', 'fork', 'workspace'):
            raise ValueError('Invalid App trash record')
        self.manager.versions._default_path(info['app_id'])
        return info

    def purge(self, archive_id: str) -> dict:
        """Permanently remove one archived repository and its own launch caches."""
        with self.manager.lock():
            info = self.trash_entry(archive_id)
            repository_id = str(uuid.UUID(info['repository_id']))
            if any(app['repository_id'] == repository_id for app in
                   self.manager.inventory(with_git=False, with_defaults=False)['apps']):
                raise ValueError('This repository is installed again; keep its Trash entry until it is removed')
            # Restores may change scope, but repository identity remains stable.
            # Never delete another source's snapshots or the App's user data.
            snapshots = [self.manager.records / 'snapshots' / scope / repository_id
                         for scope in ('workspace', 'user', 'fork')]
            if (info.get('record') or {}).get('origin') == 'bundled':
                marker = self.manager.records / 'bundled-migration' / f"{info['app_id']}.json"
                migration = json.loads(marker.read_text()) if marker.is_file() and not marker.is_symlink() else {}
                if migration.get('repository_id') != repository_id:
                    raise ValueError('Cannot verify the migrated repository; keep this App in Trash')
                snapshots.extend([
                    self.manager.records / 'repositories' / 'builtin' / info['app_id'],
                    self.manager.records / 'snapshots' / 'builtin' / repository_id,
                ])
                # Keep the migration marker so an old image cannot reinstall it.
            for path in snapshots:
                if any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)) or not path.resolve().is_relative_to(self.manager.records.resolve()):
                    raise ValueError('App snapshot directory is outside its managed root')
            for path in snapshots:
                if path.exists():
                    shutil.rmtree(path)
            shutil.rmtree(self.trash / archive_id)
            return {'success': True, 'archive_id': archive_id}

    def restore(self, archive_id: str) -> dict:
        if not re.fullmatch(r'[a-f0-9]{32}', archive_id):
            raise ValueError('Invalid App trash entry')
        with self.manager.lock():
            archive = self.trash / archive_id
            info = self.trash_entry(archive_id)
            app_id = info['app_id']
            self.manager.versions._default_path(app_id)  # validate before using it as a path
            if any(a['repository_id'] == info.get('repository_id') for a in self.manager.inventory()['apps']):
                raise ValueError('This App already has a personal branch. Remove it before restoring another.')
            has_original = any(a['id'] == app_id for a in self.manager.inventory()['apps'])
            scope = 'fork' if has_original or info['scope'] == 'fork' else 'user'
            root = self.root if scope == 'fork' else self.manager.user_root
            if info['scope'] == 'workspace':
                original = Path(info['source_dir'])
                if original.parent.resolve() not in [r.resolve() for r, kind in self.manager.roots if kind == 'workspace']:
                    raise ValueError('Original workspace is unavailable; keep this App in Trash')
                root, scope = original.parent, 'workspace'
            if Path(info.get('directory_name', app_id)).name != info.get('directory_name', app_id):
                raise ValueError('Invalid App restore directory')
            root.mkdir(parents=True, exist_ok=True)
            destination = root / info.get('directory_name', app_id)
            if destination.exists() or destination.is_symlink():
                destination = root / info.get('repository_id', archive_id)
            if destination.exists() or destination.is_symlink():
                raise ValueError('Restore destination is occupied; keep the archived repository in Trash')
            # Restoring never silently overrides the official launch target.
            (archive / 'app').rename(destination)
            try:
                self.manager._save(destination.name, {**info['record'], 'repository_id': info.get('repository_id'), 'restored_at': datetime.now(timezone.utc).isoformat()}, scope)
            except Exception:
                destination.rename(archive / 'app')
                raise
            shutil.rmtree(archive)
            return {'success': True, 'app_id': app_id, 'scope': scope, 'repository_id': info.get('repository_id')}
