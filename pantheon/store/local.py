"""Workspace-aware Store package records; no Agent state or process cwd."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pantheon.platform.registry_lock import registry_lock
from .installer import PackageInstaller, _skill_install_root


class LocalPackages:
    def __init__(self, work_dir: Path, manifest: Path | None = None):
        self.work_dir = work_dir.resolve()
        self.manifest = manifest or Path.home() / '.pantheon' / 'store_installs.json'

    def _lock(self):
        return registry_lock(self.manifest.with_suffix('.lock'))

    def _load(self):
        try:
            value = json.loads(self.manifest.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict) or any(not isinstance(item, dict) for item in value.values()):
            raise ValueError('Invalid Store installation registry; restore it before changing packages')
        for item in value.values():
            if '_legacy_install' in item and not isinstance(item['_legacy_install'], dict):
                raise ValueError('Invalid legacy Store installation record')
            locations = item.get('_install_locations', {})
            if not isinstance(locations, dict) or any(
                not isinstance(root, str) or not Path(root).is_absolute() or not isinstance(meta, dict)
                for root, meta in locations.items()
            ):
                raise ValueError('Invalid Store installation locations')
        return value

    def _save(self, value):
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.manifest.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.manifest)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @staticmethod
    def _legacy(record):
        return record.get('_legacy_install') if '_install_locations' in record else record

    @staticmethod
    def _pack(locations, legacy=None):
        # Retain the legacy package-id map and top-level display metadata. All
        # mutations use the explicit owner map, never the most recent root.
        latest = next(reversed(locations.values()), legacy or {})
        return {**latest, '_install_locations': locations,
                **({'_legacy_install': legacy} if legacy else {})}

    @staticmethod
    def _relative(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Package paths must be non-empty relative paths')
        parts = value.replace('\\', '/').split('/')
        if value.startswith('/') or any(p in ('', '.', '..') or ':' in p for p in parts):
            raise ValueError(f'Invalid package path: {value}')
        return value

    @classmethod
    def _validate(cls, root, data):
        kind, name = data.get('type'), cls._relative(data.get('name'))
        if kind not in ('skill', 'agent', 'team', 'app'):
            raise ValueError(f'Unknown package type: {kind}')
        base = root / '.pantheon'
        if kind == 'skill':
            rel = 'skills/' + cls._relative(data['path']) if data.get('path') else _skill_install_root(name)
            targets = [base / rel / 'SKILL.md']
        elif kind in ('agent', 'team'):
            targets = [base / (kind + 's') / (name + '.md')]
        else:
            targets = [base / 'apps' / name]
        for path in (data.get('files') or {}):
            relpath = cls._relative(path)
            if kind == 'app':
                relpath = relpath.removeprefix(name + '/')
                targets.append(base / 'apps' / name / relpath)
            else:
                if kind == 'skill' and relpath.startswith(f'skills/{name}/'):
                    relpath = rel + '/' + relpath[len(f'skills/{name}/'):]
                if not relpath.startswith(('skills/', 'agents/', 'teams/')):
                    raise ValueError(f'Package file is outside content directories: {path}')
                targets.append(base / relpath)
        for target in targets:
            resolved = target.resolve()
            if base.resolve() not in resolved.parents:
                raise ValueError(f'Package path escapes its installation directory: {target}')
        return targets

    @classmethod
    def _exists(cls, root, info):
        targets = cls._validate(root, info)
        if any(path.exists() for path in targets):
            return True
        if info.get('type') == 'skill':
            # Recognize pre-source-folder installs without deleting their records.
            name = cls._relative(info['name'])
            return (root / '.pantheon/skills' / name / 'SKILL.md').exists() or (
                root / '.pantheon/skills' / (name + '.md')).exists()
        return False

    def install(self, package_id, download):
        with self._lock():
            records = self._load()  # refuse corrupt state before writing files
            self._validate(self.work_dir, download)
            written = PackageInstaller(work_dir=self.work_dir).install(
                download['type'], download['name'], download['content'],
                download.get('files'), path=download.get('path'))
            old = records.get(package_id, {})
            locations = dict(old.get('_install_locations', {}))
            locations.pop(str(self.work_dir), None)
            locations[str(self.work_dir)] = {
                'name': download['name'], 'type': download['type'],
                'version': download['version'], 'path': download.get('path'),
                'work_dir': str(self.work_dir),
                'installed_at': datetime.now(timezone.utc).isoformat(),
            }
            records[package_id] = self._pack(locations, self._legacy(old))
            try:
                self._save(records)
            except Exception as error:
                raise RuntimeError('Package files were installed, but their installation record could not be saved; retry the installation after fixing storage. ' + str(error)) from error
            return {'success': True, 'name': download['name'], 'type': download['type'],
                    'version': download['version'], 'installed_files': [str(p) for p in written]}

    def list(self):
        # Listing is read-only: a different active project or an unavailable
        # volume must never be interpreted as permission to prune records.
        records = self._load()
        visible = {}
        for package_id, record in records.items():
            locations = record.get('_install_locations', {})
            for root in dict.fromkeys((self.work_dir, Path.home().resolve())):
                info = locations.get(str(root))
                if info:
                    visible[package_id] = {**info, 'available': self._exists(root, info)}
                    break
            else:
                legacy = self._legacy(record)
                if legacy and any(self._exists(root, legacy) for root in
                                  dict.fromkeys((self.work_dir, Path.home().resolve()))):
                    visible[package_id] = {**legacy, 'legacy_unscoped': True}
        return {'success': True, 'installs': visible}

    def uninstall(self, package_id):
        with self._lock():
            records = self._load()
            record = records.get(package_id, {})
            locations = dict(record.get('_install_locations', {}))
            root = next((root for root in dict.fromkeys((self.work_dir, Path.home().resolve()))
                         if str(root) in locations), None)
            legacy_only = root is None
            if legacy_only:
                legacy = self._legacy(record)
                candidates = [candidate for candidate in dict.fromkeys((self.work_dir, Path.home().resolve()))
                              if legacy and self._exists(candidate, legacy)]
                if len(candidates) > 1:
                    raise ValueError('Legacy install exists in both project and global directories; its owner must be resolved before removal')
                if not candidates:
                    raise ValueError('Package is not installed in this project')
                root = candidates[0]
                info = legacy
            else:
                info = locations[str(root)]
            self._validate(root, info)
            removed = PackageInstaller(work_dir=root).uninstall(info['type'], info['name'], path=info.get('path'))
            if not legacy_only:
                del locations[str(root)]
            legacy = self._legacy(record)
            if locations or legacy:
                records[package_id] = self._pack(locations, legacy)
            else:
                del records[package_id]
            try:
                self._save(records)
            except Exception as error:
                raise RuntimeError('Package files were removed, but their installation record could not be saved; retry removal after fixing storage. ' + str(error)) from error
            return {'success': True, 'name': info['name'], 'removed_files': [str(p) for p in removed]}
