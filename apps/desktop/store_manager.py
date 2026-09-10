"""User-owned App repositories and Store installs, on the Desktop's filesystem."""
from __future__ import annotations

import fcntl
import json
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pantheon.apps.store_release import git, git_history, prepare_release, unpack_release, validate_manifest
from pantheon.apps.versioning import fork, publish
from pantheon.apps.versioning import _match_range


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

    def inventory(self) -> dict:
        apps, warnings, seen = [], [], set()
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
                    app_id = manifest["id"]
                    # ids are also record filenames; malformed packages stay visible as warnings.
                    import re
                    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(app_id)):
                        raise ValueError("invalid App id")
                    record = self._record(directory.name, scope) if scope in ("user", "fork", "workspace") else {}
                    repository = self.versions.repository(directory, scope, app_id)
                    repo = self._git_info(repository, record)
                    repo["path"] = str(repository) if repo.get("independent") else None
                    repository_id = record.get('repository_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, str(repository.resolve())))
                    apps.append({"id": app_id, "manifest": manifest, "scope": scope,
                                 "dir": str(directory), "repository_id": repository_id, "record_key": directory.name,
                                 "repository": {"id": repository_id, "visibility": 'public' if record.get('published') or record.get('origin') == 'store' else 'private',
                                                "upstream": record.get('upstream'), "publication": record.get('published'),
                                                "label": record.get('label')}, "effective": scope != "fork" and app_id not in seen,
                                 "default": self.versions.default(app_id),
                                 "git": repo, "install": record or None})
                    seen.add(app_id)
                except (ValueError, KeyError, OSError) as exc:
                    warnings.append(f"Cannot inspect {scope}/{directory.name}: {exc}")
        return {"success": True, "apps": apps, "warnings": warnings, "user_root": str(self.user_root)}

    @staticmethod
    def _git_info(directory: Path, record: dict) -> dict:
        if not (directory / ".git").exists():
            return {"independent": False, "modified": False}
        try:
            commit = git(directory, "rev-parse", "HEAD").strip()
            changes = git(directory, "status", "--porcelain").splitlines()
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
                    "changes": changes, "tags": git(directory, "tag", "--list", "--sort=-version:refname").splitlines(),
                    "branch": git(directory, "branch", "--show-current").strip(),
                    "updated_at": git(directory, "log", "-1", "--format=%cI").strip(),
                    "modified": bool(changes) or extra_commits or bool(baseline and baseline != commit)}
        except ValueError as exc:
            return {"independent": True, "modified": True, "error": str(exc)}

    def find(self, app_id: str, scope: str | None = None, repository_id: str = '') -> dict:
        candidates = [a for a in self.inventory()['apps'] if a['id'] == app_id and
                      (scope is None or a['scope'] == scope) and (not repository_id or a['repository_id'] == repository_id)]
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
            effective = {a["id"]: a["manifest"] for a in self.inventory()["apps"] if a["effective"]}
            effective[app_id] = manifest
            for owner_id, owner in effective.items():
                for dep_id, spec in (owner.get("dependencies") or {}).items():
                    if owner_id != app_id and dep_id != app_id:
                        continue
                    required = spec.get("range", "*") if isinstance(spec, dict) else str(spec)
                    dependency = effective.get(dep_id)
                    if not dependency or not _match_range(dependency.get("version", "0.0.0"), required):
                        raise ValueError(f"{owner_id} requires {dep_id} {required}; install a compatible dependency first")
            repository = download.get('repository') or {}
            repository_id = repository.get('id') or download['package_id']
            try:
                repository_id = str(uuid.UUID(repository_id))
            except ValueError:
                repository_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'store-package:' + repository_id))
            previous = next((a for a in self.inventory()['apps'] if a['scope'] == 'user' and
                             (a.get('install') or {}).get('package_id') == download['package_id']), None)
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
                self._save(target.name, {"repository_id": repository_id, "published": repository,
                                   "package_id": download["package_id"], "version": version,
                                   "origin": "store", "installed_commit": git(target, "rev-parse", "HEAD").strip(),
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

    def history(self, app_id: str, scope: str, limit: int = 100, repository_id: str = '') -> dict:
        app = self.find(app_id, scope, repository_id)
        if not app["git"].get("independent"):
            self.versions.ensure()
        root = self.versions.repository(Path(app["dir"]), scope, app_id)
        if not (root / ".git").exists():
            return {"success": True, "commits": [], "refs": [], "has_more": False,
                    "message": "The App repository could not be initialized. Refresh the installed list for details."}
        return git_history(root, limit)

    def tag(self, app_id: str, version: str, scope: str = "user", repository_id: str = "") -> dict:
        with self.lock():
            if scope not in ("workspace", "user", "fork"):
                raise ValueError("Fork the App before editing official releases")
            app = self.find(app_id, scope, repository_id)
            if (app.get('install') or {}).get('origin') == 'store':
                raise ValueError('Fork the public repository before creating your own versions')
            root = Path(app["dir"])
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
