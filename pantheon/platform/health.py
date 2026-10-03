"""Host and Fleet telemetry shared by the platform and legacy Agent host.

Health reads cached node/disk observations and never starts an App or Agent.
Agent Run activity remains the Agent App's responsibility.
"""
import asyncio
try:
    import psutil as _psutil
    _psutil_process = _psutil.Process()
except ImportError:
    _psutil = None  # type: ignore
    _psutil_process = None

# How long after boot to leave the workspace disk walk alone.
#
# The first `_ping` from a desktop arrives seconds after the agent answers,
# when the volume's FUSE cache is empty and every stat is a network round trip.
# Walking then is both the most expensive the walk will ever be and the worst
# possible moment: the user is opening windows and typing into a terminal.
# Nothing waits on a disk-usage figure, so it can start after the rush.
_DISK_WALK_QUIET_SECONDS = 120.0


class PlatformHealth:
    # The walk yields the GIL every this many entries, for this long. 200/1 ms
    # costs a few percent of walk time on a tree of a few thousand files and
    # takes the thread from monopolising a core to sharing one.
    _WALK_YIELD_EVERY = 200
    _WALK_YIELD_SECONDS = 0.001

    @classmethod
    def _walk_workspace_bytes(cls, root: str) -> int:
        """Sum file sizes under `root`. Runs in a daemon thread, and yields.

        The thread was added so the walk would not block the event loop, on the
        reasoning that os.scandir/stat release the GIL during the syscall. That
        holds for the syscalls; it does not hold for the walk. Sampled with
        py-spy on a sandbox 30 seconds old, this thread was at **96% CPU** —
        the per-entry Python work (iteration, path building, the stack) runs
        with the GIL held, and on a two-core sandbox the event loop then has to
        contend for it. A `_ping`, which is answered by the RPC framework and
        touches no toolset, took **1674 ms** during that window against a
        68 ms median once things settled.

        So the walk now yields: a short sleep every so many entries, which
        hands the GIL to the event loop and caps this thread's share. The walk
        takes marginally longer and stops being felt, which is the right trade
        for a disk-usage figure nobody is waiting on.
        """
        import os
        import time as _t

        # Do not walk the package prefix. `<workspace>/.local` holds the
        # environments a user installs into — one conda env is on the order of
        # 11k files — and on a network-backed Volume that is tens of thousands
        # of stat calls the event loop has to contend with. Keystroke latency
        # went from 64 ms to 220-515 ms once the first conda env existed there,
        # with one pty_write at 1.0 s.
        #
        # Yielding, added when this walk was first found blocking the loop,
        # bounds the damage per entry; it cannot bound a number of entries that
        # grew by two orders of magnitude. Nothing under .local is user data —
        # it is installed packages, and a disk-usage figure that counts them
        # tells nobody anything they wanted to know.
        _SKIP = {".local", ".cache", ".git"}

        used = 0
        seen = 0
        stack = [root]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        seen += 1
                        # Sleeping — not just `pass` — is what actually releases
                        # the GIL to a waiting thread.
                        if seen % cls._WALK_YIELD_EVERY == 0:
                            _t.sleep(cls._WALK_YIELD_SECONDS)
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in _SKIP:
                                    continue
                                stack.append(entry.path)
                            else:
                                used += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return used

    def _get_host_metrics(self) -> dict:
        """Non-blocking telemetry; filesystem/network refresh runs separately."""
        metrics = {}
        if _psutil is not None and _psutil_process is not None:
            try:
                metrics["cpu_percent"] = round(_psutil_process.cpu_percent(interval=None), 1)
                rss = _psutil_process.memory_info().rss
                total = _psutil.virtual_memory().total
                metrics["mem_used_mb"] = round(rss / 1024 / 1024, 1)
                metrics["mem_percent"] = round(rss / total * 100, 1) if total > 0 else 0.0
                # Static allocation snapshot — what the runtime actually
                # has, so the UI can show "12.3% of 8 vCPU · 1.2/12 GiB"
                # without having to also know what Hub configured.
                metrics["cpu_count"] = _psutil.cpu_count(logical=True)
                metrics["mem_total_mb"] = round(total / 1024 / 1024, 1)
            except Exception:
                pass  # process may have exited or psutil failed — omit silently

            try:
                # Disk usage of the user's workspace mount (the only space
                # users actually fill with their data). Only report when
                # the hosted /workspace mount exists — local runs don't
                # have a quota to report against.
                #
                # psutil.disk_usage / statvfs on a Modal Volume returns the
                # underlying host disk (hundreds of GiB, used=0), not the
                # Volume itself, so we walk the tree ourselves and compare
                # against a configured quota (default 10 GiB per user).
                # Cached 30s so _ping stays cheap.
                import os as _os
                import time as _time
                import threading as _threading
                # The transitional Agent child shares the platform's state host.
                # Only the platform should scan that filesystem.
                if _os.environ.get("PANTHEON_STATE_SYNC_OWNER") != "external" and _os.path.isdir("/workspace"):
                    quota_mb = float(_os.environ.get("WORKSPACE_QUOTA_MB", 10 * 1024))
                    now = _time.monotonic()
                    cached = getattr(self, "_disk_used_cache", None)
                    # CRITICAL: never walk the tree inline. This method runs
                    # synchronously on the event-loop thread (NATSRemoteWorker
                    # ._ping). On big volumes the walk is thousands of FUSE
                    # stat() round-trips taking 10-15s, which would freeze
                    # EVERY NATS RPC in this process — chat AND the Endpoint's
                    # read_chunk_at — stalling file downloads. So refresh the
                    # cache in a detached daemon thread and only ever READ the
                    # cached value here. A single-flight flag prevents pile-up.
                    # Not during the sandbox's opening minutes. The first
                    # `_ping` a desktop sends arrives seconds after boot, when
                    # the FUSE cache is empty and every stat is a round trip —
                    # so the very first walk is both the most expensive one and
                    # the one that lands while the user is opening windows and
                    # typing. Nothing waits on a disk figure; it can start
                    # after the rush.
                    too_young = (now - getattr(self, "_started_monotonic", now)) < _DISK_WALK_QUIET_SECONDS
                    if (
                        not too_young
                        and (cached is None or now - cached[1] > 300.0)
                        and not getattr(self, "_disk_walk_running", False)
                    ):
                        self._disk_walk_running = True

                        def _refresh_disk_used():
                            try:
                                used = self._walk_workspace_bytes("/workspace")
                                self._disk_used_cache = (used, _time.monotonic())
                            except Exception:
                                pass
                            finally:
                                self._disk_walk_running = False

                        _threading.Thread(
                            target=_refresh_disk_used, name="disk-usage-walk", daemon=True
                        ).start()
                    if cached is not None:
                        used_mb = cached[0] / 1024 / 1024
                        metrics["disk_used_mb"] = round(used_mb, 1)
                        metrics["disk_total_mb"] = round(quota_mb, 1)
                        metrics["disk_percent"] = round(used_mb / quota_mb * 100, 1) if quota_mb > 0 else 0.0
            except Exception:
                pass  # filesystem walk failed — omit silently

        nodes = self._fleet_nodes_snapshot()
        if nodes:
            metrics["fleet_nodes"] = nodes

        return metrics

    _FLEET_NODES_TTL = 5.0

    def _fleet_nodes_snapshot(self) -> list[dict]:
        """Per-node load snapshot for _ping, from the fleet registry.

        Runners heartbeat their Node record (incl. normalized CPU/mem load)
        into the registry KV every 10s; this trims those records down to
        what a status UI needs. Synchronous and non-blocking like the rest
        of the activity path: reads a cache and kicks a single-flight async
        refresh on the running loop when the cache is stale.
        """
        import time as _time

        cached = getattr(self, "_fleet_nodes_cache", None)
        now = _time.monotonic()
        if (cached is None or now - cached[1] > self._FLEET_NODES_TTL) and not getattr(
            self, "_fleet_nodes_refreshing", False
        ):
            try:
                import asyncio as _asyncio

                self._fleet_nodes_refreshing = True
                self._fleet_nodes_task = _asyncio.get_running_loop().create_task(self._refresh_fleet_nodes())
            except Exception:
                self._fleet_nodes_refreshing = False
        return cached[0] if cached is not None else []

    async def _stop_health_refresh(self) -> None:
        task = getattr(self, "_fleet_nodes_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _refresh_fleet_nodes(self) -> None:
        import time as _time

        try:
            from pantheon.apps.resolver import get_shared_resolver

            resolver = get_shared_resolver()
            if resolver is None:
                return
            records = await asyncio.wait_for(
                resolver._list_nodes(max_age=self._FLEET_NODES_TTL), timeout=5)
            trimmed = []
            for rec in records:
                cap = rec.get("capability") or {}
                state = rec.get("state") or {}
                load = state.get("load") or {}
                trimmed.append({
                    "name": rec.get("name"),
                    "kind": rec.get("kind"),
                    "caps": cap.get("caps") or [],
                    "cpu_cores": cap.get("cpu_cores"),
                    "ram_gb": cap.get("ram_gb"),
                    "load_cpu": round(float(load.get("cpu") or 0.0), 3),
                    "load_mem": round(float(load.get("mem") or 0.0), 3),
                    "last_seen": rec.get("last_seen"),
                })
            self._fleet_nodes_cache = (trimmed, _time.monotonic())
        except Exception:
            pass  # registry unreadable — keep whatever snapshot we had
        finally:
            self._fleet_nodes_refreshing = False
