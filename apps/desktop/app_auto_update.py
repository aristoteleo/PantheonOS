"""Store Apps follow their Store repository's newest release (docs/apps/app-auto-update.md §4).

Every installed App with a Store upstream has an update policy in its install
record: **follow** (the default) or **pinned**. The Desktop's updater asks the
Store for the newest release of each followed repository, advances the
install's `official` branch to it (the same fast-forward as Pull update),
checks the new version can launch, and otherwise puts `official` back,
records the release as failed (never retried) and leaves a notice. An App the
user changed (own commits, uncommitted files, launching another branch) is
never touched: it shows Update available.

The functions here are synchronous and take the library manager; the async
pass (Store requests, running backends, notifications) is `run_pass`.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from pantheonos.appkit.store_release import git
from pantheonos.utils.log import logger

FOLLOW, PINNED = 'follow', 'pinned'
FIRST_PASS_SECONDS = 60
INTERVAL_SECONDS = 600
MAX_NOTICES = 50
MAX_FAILED = 20
BACKEND_READY_SECONDS = 180


def _now():
    return datetime.now(timezone.utc).isoformat()


def upstream_of(app: dict) -> dict:
    record = app.get('install') or {}
    upstream = record.get('published') if record.get('origin') == 'store' else record.get('upstream')
    return upstream or {}


def _settings(app: dict) -> dict:
    value = (app.get('install') or {}).get('auto_update')
    return dict(value) if isinstance(value, dict) else {}


def policy_of(manager, app: dict) -> str:
    """The App's policy; without one, an explicit pinned launch default of
    this repository means the user chose that version (§3.1 migration)."""
    policy = _settings(app).get('policy')
    if policy in (FOLLOW, PINNED):
        return policy
    selected = manager.versions.default(app['id'])
    if selected and selected.get('mode') != 'latest' and selected.get('commit') and \
            selected.get('repository_id') in (app['repository_id'], (app.get('install') or {}).get('repository_id')):
        return PINNED
    return FOLLOW


def official_branch(app: dict) -> str:
    record = app.get('install') or {}
    if record.get('branch_model'):
        return record.get('official_branch') or 'official'
    return 'main' if record.get('origin') == 'store' else ''


def current_commit(app: dict) -> str:
    """The commit the install has of its upstream (its official branch head)."""
    branch = official_branch(app)
    if not branch:
        return ''
    try:
        return git(Path(app['dir']), 'rev-parse', f'refs/heads/{branch}').strip()
    except ValueError:
        return ''


def modified(manager, app: dict) -> str:
    """Why the install counts as the user's own work ('' when it does not)."""
    root = Path(app['dir'])
    if not (root / '.git').exists():
        return 'not a Git repository'
    branch = official_branch(app)
    try:
        if git(root, 'status', '--porcelain').strip():
            return 'it has uncommitted changes'
        if (app.get('install') or {}).get('branch_model'):
            if git(root, 'rev-list', '-n', '1', '--branches', '--not', f'refs/heads/{branch}').strip():
                return 'it has commits of your own'
            # The branch new launches follow: an explicit default of this
            # repository, else its development branch (launch_default).
            selected = manager.versions.default(app['id']) or {}
            mine = selected.get('repository_id') == app['repository_id']
            launching = (selected.get('branch') if mine and selected.get('mode') == 'latest'
                         else '' if mine else (app.get('install') or {}).get('work_branch'))
            if launching and launching != branch:
                return f"it launches your branch {launching}"
        else:
            current = git(root, 'branch', '--show-current').strip()
            if current != branch:
                return f'it is on branch {current or "(detached)"}'
            baseline = (app.get('install') or {}).get('installed_commit')
            if baseline and git(root, 'rev-list', '-n', '1', 'HEAD', '--not', baseline).strip():
                return 'it has commits of your own'
    except ValueError as exc:
        return f'its repository cannot be read ({exc})'
    return ''


def followed(manager) -> list[dict]:
    """Installed Apps with a Store upstream (install records only; no Git status)."""
    apps = manager.inventory(with_git=False, with_defaults=False)['apps']
    return [a for a in apps if a['scope'] in ('user', 'fork', 'workspace') and upstream_of(a).get('id')
            and official_branch(a)]


def _save_settings(manager, app: dict, change) -> dict:
    record = dict(manager._record(app['record_key'], app['scope']))
    settings = change(dict(record.get('auto_update') or {}))
    record['auto_update'] = settings
    manager._save(app['record_key'], record, app['scope'])
    app['install'] = record
    return settings


def note_default(manager, app_id: str, scope: str, repository_id: str, version: str):
    """A launch default chosen by the user decides the policy too: a version
    pins, following official follows; another branch is the user's own work."""
    try:
        app = manager.find(app_id, scope or None, repository_id)
    except ValueError:
        return
    if not upstream_of(app).get('id'):
        return
    branch = official_branch(app)
    if version in ('latest', f'branch:{branch}'):
        policy = FOLLOW
    elif version.startswith('branch:'):
        return
    else:
        policy = PINNED
    with manager.lock():
        _save_settings(manager, app, lambda s: {**s, 'policy': policy})


def set_policy(manager, app_id: str, scope: str, repository_id: str, policy: str, version: str = '') -> dict:
    """Follow Store (launch the head of official) or Pin a version (default: the one it launches now)."""
    if policy not in (FOLLOW, PINNED):
        raise ValueError('policy is follow or pinned')
    app = manager.find(app_id, scope or None, repository_id)
    if not upstream_of(app).get('id'):
        raise ValueError('This App has no Store repository to follow')
    branch = official_branch(app)
    if policy == FOLLOW:
        target = f'branch:{branch}' if (app.get('install') or {}).get('branch_model') else 'latest'
    else:
        target = version or (manager.versions.launch_default(app_id) or {}).get('commit') or current_commit(app)
    manager.versions.set_default(app_id, app['scope'], target, app['repository_id'])
    with manager.lock():
        settings = _save_settings(manager, app, lambda s: {**s, 'policy': policy})
    return {'success': True, 'app_id': app_id, 'policy': settings['policy']}


def describe(manager, app: dict, head: dict | None = None) -> dict:
    """One App's update state for the Store."""
    settings = _settings(app)
    policy = policy_of(manager, app)
    commit = current_commit(app)
    upstream = upstream_of(app)
    why = modified(manager, app)
    failed = settings.get('failed') or {}
    head_commit = (head or {}).get('head') or ''
    available = bool(head_commit and head_commit != commit)
    if head_commit and head_commit in failed:
        state = 'failed'
    elif why and available:
        state = 'modified'
    elif policy == PINNED:
        state = 'pinned'
    elif available:
        state = 'pending'
    else:
        state = 'up_to_date'
    selected = manager.versions.default(app['id']) or {}
    return {'app_id': app['id'], 'scope': app['scope'], 'repository_id': app['repository_id'],
            'upstream_id': upstream.get('id'), 'policy': policy, 'state': state, 'modified': why,
            'commit': commit, 'version': upstream.get('version') or app['manifest'].get('version'),
            'available': {'commit': head_commit, 'version': (head or {}).get('version')} if available else None,
            'pinned': {'commit': selected.get('commit'), 'version': selected.get('version')}
            if policy == PINNED and selected.get('repository_id') == app['repository_id'] else None,
            'applied': settings.get('applied'),
            'failed': [{'commit': c, **info} for c, info in failed.items()]}


def _check(manager, app: dict, commit: str):
    """Raise when the version at `commit` cannot launch (§4.3)."""
    root = manager.versions.repository(Path(app['dir']), app['scope'], app['id'])
    manifest = manager.versions._manifest(root, commit)
    if manifest['id'] != app['id']:
        raise ValueError('the release belongs to another App')
    if manager.versions.restriction(manifest):
        return  # node-managed runtimes are not launched from a snapshot
    resolved = manager.versions.resolve(app['id'], app['scope'], commit, app['repository_id'])
    root = Path(resolved['dir']).resolve()
    for name in ('frontend', 'backend'):
        entry = (manifest.get('entry') or {}).get(name)
        if not isinstance(entry, str) or not entry or ':' in entry or entry.startswith(('http://', 'https://')):
            continue
        path = (root / entry.split('?', 1)[0]).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError(f'its {name} entry {entry} is not in the release')


def _move_official(root: Path, branch: str, commit: str):
    if git(root, 'branch', '--show-current').strip() == branch:
        git(root, 'reset', '-q', '--keep', commit)
    else:
        git(root, 'update-ref', f'refs/heads/{branch}', commit)


def update(manager, app: dict, repository: dict, clone_url: str) -> dict:
    """Advance a followed install to `repository['head']`, check it, and on
    failure put it back and record the release as failed."""
    from .app_upstream import sync_upstream
    head = repository['head']
    before = current_commit(app)
    record_before = dict(app.get('install') or {})
    branch = official_branch(app)
    result = sync_upstream(manager, app, repository, clone_url, pull=True, expected_commit=head)
    app = manager.find(app['id'], app['scope'], app['repository_id'])
    try:
        _check(manager, app, current_commit(app))
    except Exception as exc:  # noqa: BLE001 - any failure to launch rolls back
        reason = str(exc) or type(exc).__name__
        with manager.lock():
            _move_official(Path(app['dir']), branch, before)
            restored = dict(manager._record(app['record_key'], app['scope']))
            for key in ('upstream', 'published', 'version', 'installed_commit'):
                if key in record_before:
                    restored[key] = record_before[key]
            manager._save(app['record_key'], restored, app['scope'])
            app['install'] = restored
            mark_failed(manager, app, head, repository.get('version'), reason, lock=False)
        add_notice(manager, {'kind': 'failed', 'app_id': app['id'], 'name': app['manifest'].get('name'),
                             'version': repository.get('version'), 'previous_version': record_before.get('version')
                             or (record_before.get('upstream') or {}).get('version'), 'reason': reason})
        return {'success': False, 'rolled_back': True, 'reason': reason, 'commit': before}
    previous = {'commit': before, 'version': (record_before.get('upstream') or {}).get('version') or record_before.get('version')}
    with manager.lock():
        _save_settings(manager, app, lambda s: {**s, 'previous': previous,
                                                'applied': {'commit': head, 'version': repository.get('version'), 'at': _now()}})
    add_notice(manager, {'kind': 'updated', 'app_id': app['id'], 'name': app['manifest'].get('name'),
                         'version': repository.get('version'), 'previous_version': previous['version']})
    return {**result, 'success': True, 'previous': previous}


def mark_failed(manager, app: dict, commit: str, version, reason: str, *, lock=True):
    def change(s):
        failed = dict(s.get('failed') or {})
        failed[commit] = {'version': version, 'reason': reason[:300], 'at': _now()}
        return {**s, 'failed': dict(sorted(failed.items(), key=lambda kv: kv[1]['at'])[-MAX_FAILED:])}
    if lock:
        with manager.lock():
            _save_settings(manager, app, change)
    else:
        _save_settings(manager, app, change)


def revert(manager, app: dict, previous: dict, failed_commit: str, version, reason: str):
    """Put a followed install back on `previous` after its new version failed to run."""
    with manager.lock():
        app = manager.find(app['id'], app['scope'], app['repository_id'])
        _move_official(Path(app['dir']), official_branch(app), previous['commit'])
        mark_failed(manager, app, failed_commit, version, reason, lock=False)
        _save_settings(manager, app, lambda s: {k: v for k, v in s.items() if k != 'applied'})
    add_notice(manager, {'kind': 'failed', 'app_id': app['id'], 'name': app['manifest'].get('name'),
                         'version': version, 'previous_version': previous.get('version'), 'reason': reason})


def _notices_path(manager) -> Path:
    return manager.records / 'update-notices.json'


def notices(manager) -> list[dict]:
    try:
        value = json.loads(_notices_path(manager).read_text())
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def add_notice(manager, notice: dict):
    items = [*notices(manager), {'id': uuid.uuid4().hex, 'at': _now(), 'acknowledged': False, **notice}][-MAX_NOTICES:]
    path = _notices_path(manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(items))
    temp.replace(path)


def acknowledge(manager, ids: list[str] | None = None) -> int:
    items = notices(manager)
    count = 0
    for item in items:
        if not item.get('acknowledged') and (ids is None or item.get('id') in ids):
            item['acknowledged'] = True
            count += 1
    if count:
        path = _notices_path(manager)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(items))
        temp.replace(path)
    return count


def plan(manager, apps: list[dict], heads: dict[str, dict]) -> list[tuple[dict, dict, str]]:
    """(app, head, decision) for each followed install: update, pinned,
    modified, failed or up_to_date."""
    out = []
    for app in apps:
        head = heads.get(upstream_of(app)['id'])
        if not head or not head.get('head') or head.get('app_id') not in (None, app['id']):
            continue
        settings = _settings(app)
        if head['head'] == current_commit(app):
            decision = 'up_to_date'
        elif head['head'] in (settings.get('failed') or {}):
            decision = 'failed'
        elif policy_of(manager, app) == PINNED:
            decision = 'pinned'
        elif modified(manager, app):
            decision = 'modified'
        else:
            decision = 'update'
        out.append((app, head, decision))
    return out


# ---- the async pass -------------------------------------------------------

async def store_heads(binding, ids: list[str]) -> tuple[dict[str, dict], str]:
    """Newest release of each repository, by id, and the Store origin."""
    from .app_store_client import store_action
    result, base = {}, ''
    for start in range(0, len(ids), 100):
        reply = await store_action(None, 'heads', '', ','.join(ids[start:start + 100]), '', '', '', '', '', '',
                                   binding=binding)
        base = reply.get('base', base)
        for item in reply.get('repositories') or []:
            result[item['id']] = item
    return result, base


async def run_pass(desktop, manager=None) -> dict:
    """One updater pass for the Desktop `desktop` (DesktopToolSet)."""
    from .store_manager import AppStoreManager
    manager = manager or AppStoreManager(desktop._app_scope_roots())
    apps = await asyncio.to_thread(followed, manager)
    if not apps:
        return {'success': True, 'checked': 0, 'outcomes': {}}
    ids = sorted({upstream_of(a)['id'] for a in apps})
    heads, base = await store_heads(getattr(desktop, '_store_binding', None), ids)
    decisions = await asyncio.to_thread(plan, manager, apps, heads)
    outcomes, changed = {}, []
    for app, head, decision in decisions:
        key = f"{app['id']}@{app['repository_id']}"
        if decision != 'update':
            outcomes[key] = decision
            continue
        clone = head.get('clone_url')
        if not clone:
            outcomes[key] = 'not public'
            continue
        try:
            result = await asyncio.to_thread(update, manager, app, head, urljoin(base + '/', clone))
        except Exception as exc:  # noqa: BLE001 - one App must not stop the others
            logger.warning(f"[app-auto-update] {key}: update failed: {exc}")
            outcomes[key] = f'error: {exc}'
            continue
        outcomes[key] = 'updated' if result.get('success') else 'rolled_back'
        changed.append(app['id'])
        logger.info(f"[app-auto-update] {key}: {outcomes[key]} -> {head.get('version')}")
    # Long-lived backends of followed Apps move to the version they follow when idle.
    for app, head, decision in decisions:
        if decision in ('update', 'up_to_date') and policy_of(manager, app) == FOLLOW:
            try:
                moved = await restart_backends(desktop, manager, app)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[app-auto-update] {app['id']}: backend restart failed: {exc}")
                moved = []
            if moved:
                outcomes[f"{app['id']}@{app['repository_id']}:backends"] = moved
                changed.append(app['id'])
    if changed:
        await notify(desktop, manager)
    return {'success': True, 'checked': len(decisions), 'outcomes': outcomes}


async def notify(desktop, manager):
    pending = [n for n in await asyncio.to_thread(notices, manager) if not n.get('acknowledged')]
    try:
        await desktop.desktop_broadcast('store.updates', {'notices': pending})
    except Exception as exc:  # noqa: BLE001 - a viewport reads them on load too
        logger.debug(f"[app-auto-update] broadcast failed: {exc}")


async def restart_backends(desktop, manager, app) -> list[str]:
    """Move idle long-lived backends of an older version of a followed App to
    the version it launches now. A busy one is left for a later pass. A new
    version that does not become ready is reverted (old backend kept)."""
    settings = _settings(manager.find(app['id'], app['scope'], app['repository_id']))
    applied, previous = settings.get('applied') or {}, settings.get('previous') or {}
    if not applied.get('commit') or not previous.get('commit'):
        return []
    resolved = await asyncio.to_thread(manager.versions.resolve, app['id'], app['scope'], applied['commit'], app['repository_id'])
    if not ((resolved['manifest'].get('entry') or {}).get('backend') or resolved['manifest'].get('execution')):
        return []
    moved = []
    # Backends the Desktop runs itself (desktop_store_manage start).
    supervisor = getattr(desktop, '_apps_supervisor', None)
    old_key = f"{app['id']}@{app['repository_id']}:{previous['commit']}"
    process = supervisor.procs.get(old_key) if supervisor else None
    if process is not None and process.proc.returncode is None and not process.pending:
        await supervisor.stop(old_key)
        try:
            await asyncio.wait_for(supervisor.call(app['id'], None, {}, 60, pinned=resolved), BACKEND_READY_SECONDS)
            moved.append(f'desktop:{applied.get("version")}')
        except Exception as exc:  # noqa: BLE001
            old = await asyncio.to_thread(manager.versions.resolve, app['id'], app['scope'], previous['commit'], app['repository_id'])
            await asyncio.to_thread(revert, manager, app, previous, applied['commit'], applied.get('version'),
                                    f'its backend did not start: {exc}')
            await supervisor.call(app['id'], None, {}, 60, pinned=old)
            return [f'reverted:{previous.get("version")}']
    # Resident Fleet instances of the previous version.
    from .agent_app_ops import instances
    from pantheonos.appkit.lifecycle import FleetLifecycle
    if getattr(desktop, '_fleet_resolver', None) is None or desktop._fleet_resolver() is None:
        return moved
    old_version = previous.get('version')
    for instance in (await instances(desktop, {app['id']})).get(app['id'], []):
        usage = instance.get('usage') or {}
        if instance.get('version') != old_version or not instance.get('keep_alive') or instance.get('state') != 'ready':
            continue
        if usage.get('windows') or usage.get('calls'):
            continue  # busy: a later pass moves it
        placement = desktop._app_placement()
        try:
            binding = await asyncio.wait_for(placement.ensure(app['id'], instance['node_id'], resolved['revision'],
                                                              timeout=BACKEND_READY_SECONDS), BACKEND_READY_SECONDS + 10)
        except Exception as exc:  # noqa: BLE001 - the old instance was never stopped
            await asyncio.to_thread(revert, manager, app, previous, applied['commit'], applied.get('version'),
                                    f'its backend did not start: {exc}')
            return [*moved, f'reverted:{previous.get("version")}']
        await desktop._app_usage(binding, keep_alive=True)
        await FleetLifecycle(placement.resolver).submit(instance['node_id'], 'stop', instance['revision'],
                                                       generation=instance['generation'])
        moved.append(f"{instance['node_id']}:{applied.get('version')}")
    return moved


class Updater:
    """The background task: a first pass soon after start, then every interval."""

    def __init__(self, desktop, first=FIRST_PASS_SECONDS, interval=INTERVAL_SECONDS):
        self.desktop, self.first, self.interval = desktop, first, interval
        self.task = None
        self.lock = asyncio.Lock()
        self.last = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def kick(self):
        """Run a pass soon (a policy changed to Follow Store)."""
        async def later():
            try:
                await self.run()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f'[app-auto-update] pass failed: {exc}')
        self.kicked = asyncio.create_task(later())

    async def run(self) -> dict:
        async with self.lock:
            self.last = await run_pass(self.desktop)
            self.last['at'] = _now()
            return self.last

    async def _loop(self):
        await asyncio.sleep(self.first)
        while True:
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f'[app-auto-update] pass failed: {exc}')
            await asyncio.sleep(self.interval)
