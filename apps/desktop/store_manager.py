"""User-owned App repositories and Store installs, on the Desktop's filesystem."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pantheonos.appkit.store_release import git, git_history, prepare_release, unpack_release, validate_manifest
from pantheonos.appkit.versioning import fork, publish
from pantheonos.appkit.versioning import _match_range


class AppStoreManager:
    def __init__(self, roots: list[tuple[Path, str]]):
        self.roots = roots
        self.user_root = next(root for root, scope in roots if scope == "user")
        self.records = self.user_root.parent / "app-store"
        from .app_versions import AppVersions
        self.versions = AppVersions(self)
        from .app_branches import AppBranches
        self.branches = AppBranches(self)

    @contextmanager
    def lock(self):
        self.records.mkdir(parents=True, exist_ok=True)
        with (self.records / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _record_path(self, app_id: str, scope: str = 'user') -> Path:
        self.versions._default_path(app_id)
        return (self.records / f'{scope}-records' if scope in ('fork', 'workspace') else self.records) / f'{app_id}.json'

    def _record(self, app_id: str, scope: str = 'user') -> dict:
        path = self._record_path(app_id, scope)
        return json.loads(path.read_text()) if path.exists() else {}

    def _save(self, app_id: str, record: dict, scope: str = 'user'):
        path = self._record_path(app_id, scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, indent=2))
        temp.replace(path)

    def inventory(self, *, match_id: str = '', match_scope: str | None = None, match_repository: str = '', with_defaults: bool = True, with_git: bool = True) -> dict:
        apps, warnings, seen = [], [], set()
        repositories = []
        for root, scope in [*self.roots, (self.records / 'installed', 'user'), (self.branches.root, 'fork')]:
            try:
                directories = sorted(root.iterdir()) if root.exists() else []
            except OSError as exc:
                warnings.append(f"Cannot read {scope} apps: {exc}")
                continue
            for directory in directories:
                if directory.name.startswith(".") or not directory.is_dir():
                    continue
                path = next((directory / n for n in ("app.json", "atrium.json") if (directory / n).is_file()), None)
                if path is None:
                    continue
                try:
                    manifest = json.loads(path.read_text())
                    from pantheonos.appkit.distribution import included, SYSTEM_APPS
                    if not included(manifest, scope):
                        continue
                    app_id = manifest["id"]
                    # ids are also record filenames; malformed packages stay visible as warnings.
                    import re
                    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(app_id)):
                        raise ValueError("invalid App id")
                    if match_id and app_id != match_id:
                        continue
                    effective = app_id not in seen
                    seen.add(app_id)
                    if match_scope is not None and scope != match_scope:
                        continue
                    record = self._record(directory.name, scope) if scope in ("user", "fork", "workspace") else {}
                    repository = self.versions.repository(directory, scope, app_id)
                    repository_id = record.get('repository_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, str(repository.resolve())))
                    if match_repository and repository_id != match_repository:
                        continue
                    # Targeted Agent reads/edits do not run Git commands for
                    # every installed App. Preserve precedence using manifests.
                    repo = {}
                    apps.append({"id": app_id, "manifest": manifest, "scope": scope,
                                 "distribution": 'system' if scope == 'builtin' and app_id in SYSTEM_APPS else 'app',
                                 "dir": str(directory), "repository_id": repository_id, "record_key": directory.name,
                                 "repository": {"id": repository_id, "visibility": 'public' if record.get('published') or record.get('origin') == 'store' else 'private',
                                                "upstream": record.get('upstream'), "publication": record.get('published'),
                                                "label": record.get('label')}, "effective": effective,
                                 "default": self.versions.default(app_id),
                                 "git": repo, "install": record or None, "_repository": repository})
                    repositories.append((repository, record))
                except (ValueError, KeyError, OSError) as exc:
                    warnings.append(f"Cannot inspect {scope}/{directory.name}: {exc}")
        if with_git and apps:
            # Repository reads are independent. Bound concurrency so a large
            # library does not serialize hundreds of Git subprocesses or spawn
            # an unbounded number of them on a small workspace node.
            cache = self._load_git_cache()
            scopes = [app['scope'] for app in apps]
            def inspect(item):
                (directory, record), scope = item
                return self._git_summary(directory, record, scope, cache)
            if len(apps) == 1:
                results = [inspect((repositories[0], scopes[0]))]
            else:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(inspect, zip(repositories, scopes)))
            changed = any(entry for _, entry in results)
            if not (match_id or match_scope is not None or match_repository):
                # A full read knows every repository; forget removed ones.
                current = {str(app['_repository']) for app in apps}
                changed = changed or any(key not in current for key in cache if key != 'version')
                cache = {key: value for key, value in cache.items() if key == 'version' or key in current}
            for app, (info, entry) in zip(apps, results):
                app['git'] = info
                if entry:
                    cache[str(app['_repository'])] = entry
            self._save_git_cache(cache, changed)
        for app in apps:
            app.pop('_repository', None)
        if with_defaults and with_git:
            defaults = {app_id: self.versions.launch_default(app_id, None if match_scope or match_repository else apps)
                        for app_id in {app['id'] for app in apps}}
            for app in apps:
                app['default'] = defaults[app['id']]
        return {"success": True, "apps": apps, "warnings": warnings, "user_root": str(self.user_root)}

    @staticmethod
    def _git_info(directory: Path, record: dict) -> dict:
        if not (directory / ".git").exists():
            return {"independent": False, "modified": False}
        try:
            info = AppStoreManager._git_refs(directory, record)
            changes = git(directory, "status", "--porcelain").splitlines()
        except ValueError as exc:
            return {"independent": True, "modified": True, "error": str(exc)}
        committed = info.pop("committed")
        return {**info, "changes": changes, "modified": bool(changes) or committed}

    @staticmethod
    def _git_refs(directory: Path, record: dict) -> dict:
        """Everything but the working tree: a function of refs, HEAD and config."""
        commit = git(directory, "rev-parse", "HEAD").strip()
        baseline = record.get("installed_commit")
        extra_commits = bool(git(directory, "rev-list", "--branches", "--not", baseline).strip()) if baseline else False
        try:
            remote = git(directory, "remote", "get-url", "origin").strip()
            # Never expose credentials embedded in a remote URL.
            from urllib.parse import urlsplit, urlunsplit
            parsed = urlsplit(remote)
            if parsed.scheme:
                remote = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
        except ValueError:
            remote = ""
        return {"independent": True, "commit": commit, "remote": remote,
                "tags": git(directory, "tag", "--list", "--sort=-version:refname").splitlines(),
                "branch": git(directory, "branch", "--show-current").strip(),
                "updated_at": git(directory, "log", "-1", "--format=%cI").strip(),
                "committed": extra_commits or bool(baseline and baseline != commit)}

    def _git_summary(self, directory: Path, record: dict, scope: str, cache: dict) -> tuple[dict, dict | None]:
        """One repository's Store summary and, when it was read again, its new cache entry.

        Refs, tags, branches and history are reused while the files Git
        rewrites for them are unchanged. Personal repositories still read
        their working tree; official copies are not edited by people, so their
        status is reused until their index or refs change.
        """
        if not (directory / ".git").exists():
            return {"independent": False, "modified": False, "path": None}, None
        official = scope == "builtin"
        state = _git_state(directory, record, official)
        hit = cache.get(str(directory))
        try:
            if state is not None and hit and hit.get("state") == state:
                info, entry = json.loads(json.dumps(hit["info"])), None
            else:
                info = self._git_refs(directory, record)
                if record.get("branch_model"):
                    from .local_repository import branches
                    manifest_id = self.versions._manifest(directory, info["commit"])["id"]
                    info["branches"] = branches(self, directory, manifest_id, record)
                if official:
                    info["changes"] = git(directory, "status", "--porcelain").splitlines()
                # A status read may refresh the index; key on what it left.
                state = _git_state(directory, record, official)
                entry = {"state": state, "info": info} if state is not None else None
            info = json.loads(json.dumps(info))
            if not official:
                info["changes"] = git(directory, "status", "--porcelain").splitlines()
        except ValueError as exc:
            return {"independent": True, "modified": True, "error": str(exc), "path": str(directory)}, None
        committed = info.pop("committed")
        info["modified"] = bool(info["changes"]) or committed
        info["path"] = str(directory)
        return info, entry

    def _load_git_cache(self) -> dict:
        try:
            cache = json.loads((self.records / "git-summaries.json").read_text())
            return cache if isinstance(cache, dict) and cache.get("version") == 1 else {"version": 1}
        except (OSError, ValueError):
            return {"version": 1}

    def _save_git_cache(self, cache: dict, changed: bool):
        if not changed:
            return
        try:
            self.records.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=self.records, prefix=".git-summaries-", delete=False) as out:
                json.dump(cache, out)
            Path(out.name).replace(self.records / "git-summaries.json")
        except OSError:
            pass  # Only a cache: the next read inspects Git again.

    def find(self, app_id: str, scope: str | None = None, repository_id: str = '') -> dict:
        # A targeted source operation must not inspect sibling repositories just
        # to decorate launch preferences. Only inventory/launch reads need those.
        if repository_id:
            alias = self.records / 'repository-aliases' / f'{str(uuid.UUID(repository_id))}.json'
            if alias.is_file():
                target = json.loads(alias.read_text())
                if target['app_id'] != app_id:
                    raise ValueError('Repository belongs to another App')
                scope, repository_id = target['scope'], target['repository_id']
        candidates = self.inventory(match_id=app_id, match_scope=scope, match_repository=repository_id, with_defaults=False)['apps']
        if len(candidates) > 1 and scope and not repository_id:
            raise ValueError('Multiple repositories match; pass repository_id from desktop_store_apps')
        return next(iter(candidates), None) or self._missing(app_id)

    @staticmethod
    def _missing(app_id):
        raise ValueError(f"App {app_id} is not installed in this scope")

    def copy_to_user(self, app_id: str, scope: str) -> dict:
        with self.lock():
            app = self.find(app_id, scope)
            if scope == "user":
                raise ValueError("This App is already in user space")
            self.user_root.mkdir(parents=True, exist_ok=True)
            destination = self.user_root / app_id
            if destination.exists():
                current = next(a for a in self.inventory()["apps"] if a["dir"] == str(destination))
                if not current.get("install") or not current["git"].get("independent") or current["git"].get("modified"):
                    raise ValueError("User copy has local changes; official updates cannot overwrite them")
            with tempfile.TemporaryDirectory(prefix="official-update-", dir=self.records) as temp:
                stage = fork(Path(app["dir"]), Path(temp) / "stage")
                if destination.exists():
                    # An official update advances the user's existing graph,
                    # rather than replacing it with another unrelated root.
                    shutil.rmtree(stage / ".git")
                    shutil.copytree(destination / ".git", stage / ".git")
                    if git(stage, "status", "--porcelain").strip():
                        tag = f"v{app['manifest']['version']}"
                        if git(stage, "tag", "--list", tag).strip():
                            raise ValueError("Official files changed without a new version; the existing tag cannot be replaced")
                        git(stage, "add", "-A")
                        git(stage, "-c", "user.name=Pantheon Store", "-c", "user.email=store@pantheon",
                            "commit", "-qm", f"Update official {app_id} to {tag}")
                        git(stage, "tag", tag)
                backup = Path(temp) / "previous"
                if destination.exists():
                    destination.rename(backup)
                try:
                    stage.rename(destination)
                    self._save(app_id, {"official_version": app["manifest"].get("version"),
                                       "installed_commit": git(destination, "rev-parse", "HEAD").strip(),
                                       "origin": scope, "installed_at": datetime.now(timezone.utc).isoformat()})
                except Exception:
                    shutil.rmtree(destination, ignore_errors=True)
                    if backup.exists():
                        backup.rename(destination)
                    raise
            return {"success": True, "app_id": app_id}

    def install(self, download: dict, expected_id: str | None = None) -> dict:
        if download.get("type") != "app":
            raise ValueError("Only App packages can be installed by the Desktop App manager")
        with self.lock(), tempfile.TemporaryDirectory(prefix="app-store-", dir=self.user_root.parent) as temp:
            stage = Path(temp) / "app"
            version = str(download.get("version", ""))
            release = download.get("app_release")
            if release:
                manifest = unpack_release(release, stage, version)
            else:
                # Legacy Store packages used text files. Import into a real per-App repo.
                files = download.get("files") or {}
                if not files:
                    raise ValueError("App package has no release or files")
                stage.mkdir()
                for relative, content in files.items():
                    if relative.startswith(f"{download.get('name')}/"):
                        relative = relative.split("/", 1)[1]
                    target = stage / relative
                    if not target.resolve().is_relative_to(stage.resolve()) or ".git" in Path(relative).parts:
                        raise ValueError("App file escapes its directory")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                path = next((stage / n for n in ("app.json", "atrium.json") if (stage / n).is_file()), None)
                if path is None:
                    raise ValueError("App manifest missing")
                manifest = validate_manifest(json.loads(path.read_text()), version)
                git(stage, "init", "-q")
                git(stage, "add", "-A")
                git(stage, "-c", "user.name=Pantheon Store", "-c", "user.email=store@pantheon", "commit", "-qm", f"Import {version}")
                git(stage, "tag", f"v{version}")
            app_id = manifest["id"]
            if expected_id and expected_id != app_id:
                raise ValueError("Release manifest identity does not match the requested App")
            # Manifests and install records decide the install; only the one
            # repository it replaces needs Git. Reading every App's status on a
            # Workspace whose volume has just started can take minutes.
            library = self.inventory(with_git=False, with_defaults=False)["apps"]
            effective = {a["id"]: a["manifest"] for a in library if a["effective"]}
            effective[app_id] = manifest
            for owner_id, owner in effective.items():
                for dep_id, spec in (owner.get("dependencies") or {}).items():
                    if owner_id != app_id and dep_id != app_id:
                        continue
                    required = spec.get("range", "*") if isinstance(spec, dict) else str(spec)
                    dependency = effective.get(dep_id)
                    if owner.get("apiVersion", 1) >= 2 and isinstance(spec, dict) and spec.get("uses"):
                        # Interface dependencies are bound to a provider when
                        # the App starts (a deployment's services), whatever
                        # copies this library holds.
                        continue
                    if not dependency or not _match_range(dependency.get("version", "0.0.0"), required):
                        raise ValueError(f"{owner_id} requires {dep_id} {required}; install a compatible dependency first")
            repository = download.get('repository') or {}
            repository_id = repository.get('id') or download['package_id']
            try:
                repository_id = str(uuid.UUID(repository_id))
            except ValueError:
                repository_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'store-package:' + repository_id))
            unified = next((a for a in library if a['id'] == app_id and
                            (a.get('install') or {}).get('branch_model') and
                            ((a.get('install') or {}).get('upstream') or {}).get('id') == repository.get('id') and repository.get('id')), None)
            if unified:
                from .local_repository import update_official
                root = Path(unified['dir'])
                commit = git(stage, 'rev-parse', 'HEAD').strip()
                git(root, 'fetch', '-q', '--no-tags', str(stage), 'HEAD:refs/remotes/upstream/main',
                    '+refs/tags/*:refs/remotes/upstream/tags/*')
                result = update_official(self, unified, repository, commit, version=version)
                return {**result, 'version': version}
            previous = next((a for a in library if a['scope'] == 'user' and
                             (a.get('install') or {}).get('package_id') == download['package_id']), None)
            if previous:
                previous['git'] = self._git_info(Path(previous['dir']), previous.get('install') or {})
            target = Path(previous['dir']) if previous else self.user_root / app_id
            if not previous and target.exists():
                target = self.records / 'installed' / str(uuid.UUID(repository_id))
            target.parent.mkdir(parents=True, exist_ok=True)
            if previous and (not previous.get("install") or not previous["git"].get("independent") or previous["git"].get("modified")):
                raise ValueError("Your App has local changes. Preserve them in a separate copy before replacing this version")
            record = self._record(target.name)
            if target.exists() and record.get("package_id") not in (None, download.get("package_id")):
                raise ValueError("Another Store package owns this App id")
            self.user_root.mkdir(parents=True, exist_ok=True)
            if target.exists() and (target / '.git').exists():
                # Keep earlier releases when upgrading or rolling back. Never move tags.
                for tag in git(target, 'tag', '--list').splitlines():
                    old = git(target, 'rev-parse', f'refs/tags/{tag}^{{commit}}').strip()
                    if git(stage, 'tag', '--list', tag).strip():
                        if git(stage, 'rev-parse', f'refs/tags/{tag}^{{commit}}').strip() != old:
                            raise ValueError(f'Immutable tag conflict: {tag}')
                    else:
                        git(stage, 'fetch', '-q', str(target), f'refs/tags/{tag}:refs/tags/{tag}')
            backup = Path(temp) / "previous"
            if target.exists():
                target.rename(backup)
            try:
                stage.rename(target)
                clone = repository.get('clone_url')
                if repository.get('id'):
                    git(target, 'checkout', '-q', '-B', 'official')
                if clone:
                    # Official/community installs are real local main branches,
                    # tracking the public repository, not detached release trees.
                    git(target, 'remote', 'add', 'upstream', clone)
                    git(target, 'checkout', '-q', '-B', 'official')
                    git(target, 'update-ref', 'refs/remotes/upstream/main', git(target, 'rev-parse', 'HEAD').strip())
                    git(target, 'config', 'branch.official.remote', 'upstream')
                    git(target, 'config', 'branch.official.merge', 'refs/heads/main')
                if repository.get('id'):
                    repository_id = str(uuid.uuid4())
                self._save(target.name, {"repository_id": repository_id, "upstream": {**repository, "commit": git(target, "rev-parse", "HEAD").strip(), "version": version},
                                   "package_id": download["package_id"], "version": version,
                                   "origin": 'local' if repository.get('id') else 'store',
                                   "branch_model": 1 if repository.get('id') else 0, "official_branch": 'official', "upstream_bound": True,
                                   "installed_commit": git(target, "rev-parse", "HEAD").strip(),
                                   "installed_at": datetime.now(timezone.utc).isoformat()})
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                if backup.exists():
                    backup.rename(target)
                raise
            return {"success": True, "app_id": app_id, "version": version, "scope": "user", "repository_id": repository_id}

    def remove(self, app_id: str, scope: str = 'user', repository_id: str = '') -> dict:
        return self.branches.remove(app_id, scope, repository_id)

    def bind_publication(self, app_id: str, scope: str, repository_id: str, repository: dict) -> dict:
        if scope not in ('user', 'fork', 'workspace'):
            raise ValueError('Only a personal repository can publish to Store')
        with self.lock():
            app = self.find(app_id, scope, repository_id)
            if repository.get('id') != app['repository_id'] or repository.get('app_id') != app_id:
                raise ValueError('Publication belongs to a different repository')
            from urllib.parse import urlsplit
            clone = repository.get('clone_url') or ''
            parsed = urlsplit(clone)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError('Store clone URL must be an absolute public HTTP URL')
            root = Path(app['dir'])
            remotes = git(root, 'remote').splitlines()
            git(root, 'remote', 'set-url' if 'store' in remotes else 'add', 'store', clone)
            record = {**(app.get('install') or {}), 'repository_id': app['repository_id'], 'published': repository}
            self._save(app['record_key'], record, scope)
        return {'success': True, 'repository_id': app['repository_id']}

    def prepare(self, app_id: str, scope: str = 'user', repository_id: str = '') -> dict:
        if scope not in ('user', 'fork', 'workspace'):
            raise ValueError('Fork the official App before publishing changes')
        with self.lock():
            app = self.find(app_id, scope, repository_id)
            upstream = (app.get('install') or {}).get('upstream') or {}
            return {"success": True, "repository_id": app['repository_id'],
                    "forked_from": {'repository_id': upstream['id'], 'version': upstream['version']} if upstream.get('id') and upstream.get('version') else None,
                    **prepare_release(Path(app["dir"]))}

    def release_of(self, app_id: str, scope: str, commit: str, repository_id: str = '') -> dict:
        """A release of the revision the user runs (any commit, tagged or
        not), e.g. to host it; the repository itself is not changed."""
        from pantheonos.appkit.store_release import commit_release
        app = self.find(app_id, scope, repository_id)
        root = self.versions.repository(Path(app["dir"]), app['scope'], app_id)
        prepared = commit_release(root, commit)
        if prepared['manifest']['id'] != app_id:
            raise ValueError('This version belongs to another App')
        return {"success": True, "repository_id": app['repository_id'], **prepared}

    def history(self, app_id: str, scope: str, limit: int = 100, repository_id: str = '') -> dict:
        app = self.find(app_id, scope, repository_id)
        if not app["git"].get("independent"):
            self.versions.ensure()
        root = self.versions.repository(Path(app["dir"]), scope, app_id)
        if not (root / ".git").exists():
            return {"success": True, "commits": [], "refs": [], "has_more": False,
                    "message": "The App repository could not be initialized. Refresh the installed list for details."}
        from .app_git import working_tree
        return {**git_history(root, limit), 'working_tree': working_tree(root),
                'repository': {'id': app['repository_id'], 'scope': scope, 'path': str(root),
                               'label': app.get('repository', {}).get('label')}}

    def tag(self, app_id: str, version: str, scope: str = "user", repository_id: str = "") -> dict:
        with self.lock():
            if scope not in ("workspace", "user", "fork"):
                raise ValueError("Fork the App before editing official releases")
            app = self.find(app_id, scope, repository_id)
            if (app.get('install') or {}).get('origin') == 'store':
                raise ValueError('Fork the public repository before creating your own versions')
            root = Path(app["dir"])
            from .local_repository import protect
            protect(app, root)
            manifest = {**app["manifest"], "version": version}
            validate_manifest(manifest)
            if f"v{version}" in app["git"].get("tags", []):
                raise ValueError("This tag already exists. Choose a new version")
            path = next(root / n for n in ("app.json", "atrium.json") if (root / n).is_file())
            original = path.read_text()
            path.write_text(json.dumps(manifest, indent=2) + "\n")
            try:
                tag = publish(root)
            except Exception:
                path.write_text(original)
                raise
            return {"success": True, "tag": tag}


def _git_state(directory: Path, record: dict, with_index: bool) -> list | None:
    """Identity of the files Git replaces when refs, HEAD, config (or index) change.

    Git writes each of them to a lock file and renames it into place, so a
    change always shows up as a new inode, size or mtime. Linked worktrees
    and reftable repositories keep refs elsewhere; they are read every time.
    """
    root = directory / ".git"
    if not root.is_dir() or (root / "reftable").exists():
        return None
    names = ["HEAD", "config", "packed-refs", *(["index"] if with_index else [])]
    paths = [root / name for name in names]
    for refs in (root / "refs" / "heads", root / "refs" / "tags"):
        for parent, folders, files in os.walk(refs):
            folders.sort()
            paths.extend(Path(parent) / name for name in sorted(files))
    state = []
    for path in paths:
        try:
            info = path.stat()
            state.append([str(path.relative_to(root)), info.st_ino, info.st_mtime_ns, info.st_size])
        except FileNotFoundError:
            state.append([str(path.relative_to(root))])
    # The baseline and branch model also decide modified and branches.
    state.append(json.dumps({k: record.get(k) for k in ("installed_commit", "branch_model", "official_branch", "upstream")}, sort_keys=True))
    return state
