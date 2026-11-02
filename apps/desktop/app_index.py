"""The installed-App index, kept on the controller's desktop-state service.

The desktop draws its launcher, dock and icons from the Apps installed here and
routes a window to the node its App is placed on (docs/os/desktop-state.md,
P2). Both used to be read from this App, so a stopped Workspace meant a
launcher without its packaged Apps and windows that did not know their node.
This App still owns the library; it writes what the desktop needs — one entry
per App with a frontend — to desktop-state whenever the library or a placement
changes, and the desktop reads it from there.

An entry: ``id``, ``name``, ``version``, ``kind`` (builtin, store or
packaged), ``scope``, ``dir``, ``manifest`` (without the parts a launcher does
not use), ``icon`` (a small data URI) or ``icon_hash`` (a larger one, sent
with ``apps_icon_put``), ``placement`` (``node_id``, ``policy``) and
``installed`` (``{node id: {digest, component}}``, the package this Workspace
last installed on each node, which a desktop can start there by itself).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from pantheonos.utils.log import logger

# An icon up to this size rides in its entry; a larger one is stored beside
# the index (the service's MaxDesktopIcon is 256 KB) and one over that is
# left out: the desktop draws the App's glyph instead.
INLINE_ICON = 4 * 1024
MAX_ICON = 250 * 1024
# The service refuses an index over 128 KB.
INDEX_BUDGET = 120 * 1024
# Manifest parts the desktop does not use to draw or open an App.
MANIFEST_DROP = ('provides', 'skills', 'readme', 'notes', 'dependencies', 'changelog', 'config')
# What is kept of a manifest when the index is still over its budget.
MANIFEST_KEEP = ('id', 'name', 'description', 'version', 'atriumApi', 'surface', 'kind', 'runtime',
                 'execution', 'entry', 'opens', 'defaultFor', 'launcher', 'icon', 'defaultSize',
                 'startupTimeoutMs', 'actions', 'menus', 'persistState', 'sharedState', 'share', 'placement')


def icon_hash(icon: str) -> str:
    """The service's name for an icon: the first 8 bytes of its SHA-256, hex."""
    return hashlib.sha256(icon.encode()).hexdigest()[:16]


def build_index(apps: list[dict], placements: dict[str, dict], store_root: Path | None = None
                ) -> tuple[list[dict], dict[str, str]]:
    """The index of `apps` (desktop_app_registry's entries) with their
    placement records, and the icons to store beside it ({App id: data URI})."""
    entries, icons = [], {}
    store_root = Path(store_root).resolve() if store_root else None
    for app in apps:
        manifest = app.get('manifest') or {}
        app_id = manifest.get('id')
        if not app_id:
            continue
        directory = Path(app.get('dir') or '/')
        kind = ('builtin' if app.get('scope') == 'builtin'
                else 'store' if store_root and directory.resolve().is_relative_to(store_root) else 'packaged')
        record = placements.get(app_id) or {}
        entry: dict[str, Any] = {
            'id': app_id, 'name': manifest.get('name') or app_id, 'version': manifest.get('version') or '',
            'kind': kind, 'scope': app.get('scope') or '', 'dir': str(directory),
            'manifest': {k: v for k, v in manifest.items() if k not in MANIFEST_DROP},
            'placement': {'node_id': record.get('node_id'), 'policy': record.get('policy') or 'on_demand'},
        }
        if isinstance(record.get('installed'), dict) and record['installed']:
            entry['installed'] = record['installed']
        icon = app.get('icon_url')
        if isinstance(icon, str) and icon.startswith('data:image/'):
            if len(icon) <= INLINE_ICON:
                entry['icon'] = icon
            elif len(icon) <= MAX_ICON:
                entry['icon_hash'] = icon_hash(icon)
                icons[app_id] = icon
        entries.append(entry)
    if len(json.dumps(entries)) > INDEX_BUDGET:
        for entry in entries:
            entry['manifest'] = {k: v for k, v in entry['manifest'].items() if k in MANIFEST_KEEP}
    return entries, icons


class AppIndexPublisher:
    """Writes the index to desktop-state when it changed since the last write.

    `changed()` may be called after anything that might change the library
    or a placement; it is cheap when nothing did. Writes run one at a time in
    the background, and a change during a write runs another after it. A
    failure is logged and retried on the next change; a service that does
    not grant `apps_put` (a deployment bound before P2) is asked once.
    """

    def __init__(self, client, build: Callable[[], tuple[list[dict], dict[str, str]]], timeout: int = 30):
        self._client = client
        self._build = build
        self._timeout = timeout
        self._sent: str | None = None
        self._task: asyncio.Task | None = None
        self._again = False
        self.disabled = False

    def changed(self) -> None:
        if self.disabled:
            return
        if self._task is not None and not self._task.done():
            self._again = True
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            pass   # no loop (a synchronous caller): the next change publishes

    async def wait(self) -> None:
        while self._task is not None and not self._task.done():
            await asyncio.shield(self._task)

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            self._again = False
            try:
                await asyncio.to_thread(self.publish)
            except Exception as exc:
                logger.warning('desktop-state: the App index was not written: {}', exc)
            if not self._again or self.disabled:
                return

    def _call(self, method: str, args: dict) -> dict:
        reply = self._client.invoke(method, args, timeout_seconds=self._timeout)
        if not isinstance(reply, dict) or not reply.get('success'):
            raise ValueError(str((reply or {}).get('error') if isinstance(reply, dict) else 'invalid reply'))
        result = reply.get('result')
        return result if isinstance(result, dict) else {}

    def publish(self) -> bool:
        """Write the index if it changed; True when something was written."""
        entries, icons = self._build()
        encoded = json.dumps(entries, sort_keys=True)
        if encoded == self._sent:
            return False
        try:
            result = self._call('apps_put', {'apps': entries})
        except Exception as exc:
            if getattr(exc, 'status', None) == 403:
                self.disabled = True
                logger.info('desktop-state: this deployment does not grant apps_put; the App index stays here')
                return False
            raise
        for app_id in result.get('missing_icons') or []:
            if app_id in icons:
                self._call('apps_icon_put', {'app_id': app_id, 'icon': icons[app_id]})
        self._sent = encoded
        logger.info('desktop-state: App index written ({} Apps, revision {})', len(entries), result.get('revision'))
        return True
