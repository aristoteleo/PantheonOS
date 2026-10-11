"""Store content APIs independent of Agent execution and learning workers."""

import asyncio
import hashlib
import os
from pathlib import Path

from pantheon.toolset import tool
from pantheon.utils.log import logger


class StoreAPI:
    def _store_workdir(self):
        project = self._projects().active_project
        if project is not None:
            return Path(project.path)
        return Path(getattr(self, 'workspace_path', Path.cwd()))

    def _local_packages(self):
        from pantheon.store.local import LocalPackages
        return LocalPackages(self._store_workdir())

    @tool
    async def install_store_package(self, package_id: str, version: str = None) -> dict:
        """Install Store content in the selected project, capturing its owner before downloading."""
        from pantheon.store.client import StoreClient
        try:
            local = await asyncio.to_thread(self._local_packages)
            client = await asyncio.to_thread(StoreClient)
            download = await client.download(package_id, version)
            result = await asyncio.to_thread(local.install, package_id, download)
            if client.auth.is_logged_in:
                try:
                    await client.record_install(package_id, download['version'])
                except Exception:
                    pass  # optional remote bookkeeping is not local installation
            return result
        except Exception as error:
            logger.error(f'Error installing store package: {error}')
            return {'success': False, 'error': str(error)}

    @tool
    async def uninstall_store_package(self, package_id: str) -> dict:
        """Remove this project's recorded installation, preserving other projects."""
        try:
            def remove():
                return self._local_packages().uninstall(package_id)
            return await asyncio.to_thread(remove)
        except Exception as error:
            return {'success': False, 'error': str(error)}

    @tool
    async def get_installed_store_packages(self) -> dict:
        """Read this project's installs without pruning other or unavailable roots."""
        try:
            return await asyncio.to_thread(lambda: self._local_packages().list())
        except Exception as error:
            return {'success': False, 'installs': {}, 'error': str(error)}

    def _skill_catalog(self):
        from pantheon.settings import get_settings
        from pantheon.skills.store import SkillStore
        settings = get_settings(self._store_workdir())
        return SkillStore(settings.skills_dir, settings.pantheon_dir / 'skills-runtime',
                          global_skills_dir=settings.global_skills_dir,
                          factory_skills_dir=settings.factory_skills_dir,
                          excluded_skills=[s.strip() for s in os.environ.get('PANTHEON_EXCLUDED_SKILLS', '').split(',') if s.strip()],
                          create_dirs=False)

    def _read_local_skills(self):
        store = self._skill_catalog()
        factory = store.factory_skills_dir
        def digest(path):
            try:
                return hashlib.sha256(path.read_bytes()).digest()
            except OSError:
                return None
        skills = []
        for header in store.scan_headers(limit=None):
            modified = False
            if header.scope != 'factory' and factory:
                original = digest(factory / header.path / 'SKILL.md')
                current = digest(header.skill_dir / 'SKILL.md')
                modified = original is not None and current is not None and original != current
            skills.append({'name': header.name, 'path': header.path, 'scope': header.scope, 'modified': modified})
        # Store seeds resource Markdown files as standalone packages too.
        seen = {skill['path'] for skill in skills}
        if factory:
            for md in sorted(factory.rglob('*.md')):
                if md.name in ('SKILL.md', 'SKILLS.md'):
                    continue
                rel = md.relative_to(factory)
                if any(part.startswith(('_', '.')) for part in rel.parts[:-1]):
                    continue
                key = rel.with_suffix('').as_posix()
                if key in seen or store._is_excluded(key):
                    continue
                seen.add(key)
                skills.append({'name': md.stem, 'path': key, 'scope': 'factory', 'modified': False})
        return {'success': True, 'skills': skills}

    @tool
    async def get_local_skills(self) -> dict:
        """List project/global/factory content without initializing a learning runtime."""
        try:
            return await asyncio.to_thread(self._read_local_skills)
        except Exception as error:
            return {'success': False, 'skills': [], 'error': str(error)}
