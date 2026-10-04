"""Agent-owned data and a read-only project snapshot supplied by its launcher.

Project IDs come from the platform/local launcher, not workspace paths or names.
Two releases may access the same workspace while using different App data mounts.
This module never imports the legacy project registry or adopts its memories.
"""
from dataclasses import dataclass, asdict
from hashlib import sha256
import os
from pathlib import Path

from pantheon.factory.instance_store import AgentInstanceStore
from pantheon.factory.instances import _identifier
from pantheon.platform.registry_lock import registry_lock
from .data_transition import require_ready


@dataclass(frozen=True)
class AppProject:
    id: str
    name: str
    path: str


class AppProjects:
    """Immutable selection for one deployment; changes require a new snapshot.

    Runtime's legacy project_name RPC argument remains supported. Names and
    paths must therefore be unambiguous in a snapshot. Stable IDs, not those
    presentation fields, identify the conversation store across restarts.
    """
    def __init__(self, projects, *, active_id=None, default_id=None):
        by_id, by_path, names = {}, {}, set()
        for item in projects:
            if not isinstance(item, dict) or set(item) != {'id', 'name', 'path'}:
                raise ValueError('Supply project IDs, names and absolute workspace paths')
            if not all(_identifier(item[key]) for key in ('id', 'name')):
                raise ValueError('Invalid Agent project identity')
            path = item['path']
            if not isinstance(path, str) or not path or not Path(path).is_absolute():
                raise ValueError('Agent project workspace must be absolute')
            # Do not stat/resolve a remote project filesystem during startup.
            # The launcher supplies its canonical path; this is not an FS grant.
            path = os.path.normpath(path)
            if item['id'] in by_id or path in by_path or item['name'] in names:
                raise ValueError('Agent project snapshot contains ambiguous identities')
            project = AppProject(item['id'], item['name'], path)
            by_id[project.id], by_path[path] = project, project
            names.add(project.name)
        if any(value is not None and value not in by_id for value in (active_id, default_id)):
            raise ValueError('Selected Agent project is absent from its snapshot')
        self._by_id, self._by_path = by_id, by_path
        self._active, self._default = by_id.get(active_id), by_id.get(default_id)

    @property
    def active_project(self):
        return self._active

    @property
    def default_project(self):
        return self._default

    def list_projects(self):
        return [asdict(project) for project in self._by_id.values()]

    def get_project(self, path):
        return self._by_path.get(os.path.normpath(path))


class AgentAppData:
    """One local writer for all conversation and instance state in a data mount.

    Hold this from before constructing AgentRuntime through its final drain.
    The instance journal persists the namespace and owns the lifetime file lock.
    Platform fencing is still required for independent mounts/replicas; this is
    not distributed locking or a migration of existing CLI/Desktop data.
    """
    def __init__(self, root, *, namespace, projects: AppProjects, model_configuration=None):
        if not isinstance(projects, AppProjects):
            raise ValueError('Agent data requires an explicit project snapshot')
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise ValueError('Agent App data directory must not be a symlink')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'posix':
            stat = self.root.stat()
            if stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
                raise ValueError('Agent App data directory must be private')
        self.projects = projects
        # Acquire before any memory manager/settings/template writes. A second
        # App pointing at this data fails even before it creates its first Agent.
        with registry_lock(self.root / 'data-admission.lock', timeout=0):
            require_ready(self.root, namespace, model_configuration)
            self.instances = AgentInstanceStore(self.root / 'instances', namespace=namespace)
        self.home_memory_dir = str(self.root / 'conversations' / 'home')

    def project_memory_dir(self, path):
        project = self.projects.get_project(path)
        if project is None:
            raise ValueError('Project is outside the Agent App snapshot')
        key = sha256(project.id.encode('utf-8')).hexdigest()
        return str(self.root / 'conversations' / 'projects' / key)

    def close(self):
        self.instances.close()
