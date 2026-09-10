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

    def fork_app(self, app_id: str, scope: str, version: str = '') -> dict:
        self.manager.versions.ensure()
        with self.manager.lock():
            app = self.manager.find(app_id, scope)
            existing = next((a for a in self.manager.inventory()['apps']
                             if a['id'] == app_id and a['scope'] in ('fork', 'user')), None)
            if existing:
                return {'success': True, 'app_id': app_id, 'scope': existing['scope'], 'existing': True}
            source = self.manager.versions.repository(Path(app['dir']), scope, app_id)
            ref = version or app['git']['commit']
            if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', ref):
                ref = f'refs/tags/{ref}'
            commit = git(source, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').strip()
            if self.manager.versions._manifest(source, commit)['id'] != app_id:
                raise ValueError('Version belongs to another App')
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / app_id
            if target.exists():
                raise ValueError('A personal branch already exists at this path')
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                stage = Path(temp) / 'app'
                # Clone the real history. No new root commit or copied version tag.
                git(source, 'clone', '-q', '--no-local', '--', str(source), str(stage))
                git(stage, 'checkout', '-q', '-B', 'my-work', commit)
                git(stage, 'remote', 'rename', 'origin', 'upstream')
                record = {'origin': 'fork', 'parent_scope': scope, 'parent_commit': commit,
                          'installed_commit': commit, 'installed_at': datetime.now(timezone.utc).isoformat()}
                stage.rename(target)
                try:
                    self.manager._save(app_id, record, 'fork')
                except Exception:
                    target.rename(stage)
                    raise
            return {'success': True, 'app_id': app_id, 'scope': 'fork', 'commit': commit}

    def remove(self, app_id: str, scope: str) -> dict:
        if scope not in ('user', 'fork'):
            raise ValueError('Only user installations and personal branches can be removed')
        with self.manager.lock():
            app = self.manager.find(app_id, scope)
            source = Path(app['dir'])
            root = self.root if scope == 'fork' else self.manager.user_root
            if source.is_symlink() or source.resolve().parent != root.resolve():
                raise ValueError('App directory is outside its managed root')
            archive_id = uuid.uuid4().hex
            archive = self.trash / archive_id
            archive.mkdir(parents=True)
            info = {'archive_id': archive_id, 'app_id': app_id, 'name': app['manifest']['name'],
                    'version': app['manifest']['version'], 'scope': scope,
                    'removed_at': datetime.now(timezone.utc).isoformat(),
                    'record': app.get('install') or {}, 'modified': app['git'].get('modified', False)}
            (archive / 'record.json').write_text(json.dumps(info, indent=2))
            source.rename(archive / 'app')
            # The entire working tree AND Git branches survive, including dirty files.
            self.manager._record_path(app_id, scope).unlink(missing_ok=True)
            if (self.manager.versions.default(app_id) or {}).get('scope') == scope:
                self.manager.versions._default_path(app_id).unlink(missing_ok=True)
            return {'success': True, 'archive_id': archive_id}

    def list_trash(self) -> dict:
        items = []
        if self.trash.exists():
            for directory in self.trash.iterdir():
                try:
                    if directory.is_symlink() or not (directory / 'app').is_dir():
                        continue
                    info = json.loads((directory / 'record.json').read_text())
                    items.append({key: info[key] for key in ('archive_id', 'app_id', 'name', 'version', 'removed_at', 'modified')})
                except (OSError, ValueError, KeyError):
                    continue
        return {'success': True, 'items': sorted(items, key=lambda item: item['removed_at'], reverse=True)}

    def restore(self, archive_id: str) -> dict:
        if not re.fullmatch(r'[a-f0-9]{32}', archive_id):
            raise ValueError('Invalid App trash entry')
        with self.manager.lock():
            archive = self.trash / archive_id
            info = json.loads((archive / 'record.json').read_text())
            app_id = info['app_id']
            self.manager.versions._default_path(app_id)  # validate before using it as a path
            if any(a['id'] == app_id and a['scope'] in ('fork', 'user') for a in self.manager.inventory()['apps']):
                raise ValueError('This App already has a personal branch. Remove it before restoring another.')
            has_original = any(a['id'] == app_id for a in self.manager.inventory()['apps'])
            scope = 'fork' if has_original or info['scope'] == 'fork' else 'user'
            root = self.root if scope == 'fork' else self.manager.user_root
            root.mkdir(parents=True, exist_ok=True)
            destination = root / app_id
            if destination.exists() or destination.is_symlink():
                raise ValueError('The restore path is already occupied')
            # Restoring never silently overrides the official launch target.
            (archive / 'app').rename(destination)
            try:
                self.manager._save(app_id, {**info['record'], 'restored_at': datetime.now(timezone.utc).isoformat()}, scope)
            except Exception:
                destination.rename(archive / 'app')
                raise
            shutil.rmtree(archive)
            return {'success': True, 'app_id': app_id, 'scope': scope}
