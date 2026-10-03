"""Independent platform lifetime with an optional transitional Agent child.

The child launcher is removed when Agent uses the ordinary App supervisor. It
does not import Agent code, share its event loop, or restart a failed Agent.
"""

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import sys

from pantheon.utils.log import logger
from . import state_sync
from .registry_lock import registry_lock


def platform_seed(identity: str) -> str:
    """Distinct service seed; the existing transport hashes this once."""
    return "platform:" + identity


def agent_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PANTHEON_STATE_URL", "PANTHEON_STATE_TOKEN"):
        env.pop(key, None)
    env["PANTHEON_STATE_SYNC_OWNER"] = "external"
    return env


class SnapshotSession:
    """One restore before serving; serialized pushes, including clean shutdown.

    Unlike the legacy loop this never restores into a live service after a
    failed startup. Waiting for the loop to finish also waits for in-flight
    file/HTTP work instead of cancelling a thread that can still publish data.
    """

    def __init__(self, home: Path):
        self.home = home
        self.config = state_sync._config()
        if os.environ.get("PANTHEON_STATE_SYNC_OWNER") != "external":
            if bool(os.environ.get("PANTHEON_STATE_URL", "").strip()) != bool(
                os.environ.get("PANTHEON_STATE_TOKEN", "").strip()
            ):
                raise RuntimeError("State sync needs both URL and token")
        self.prepared = False
        self.digest = None

    def prepare(self):
        if self.config:
            state_sync._restored = False
            state_sync.restore(self.home)
            if not state_sync._restored:
                raise RuntimeError("Platform state restore failed; refusing to start writers")
        self.digest = state_sync._digest(self.home) if self.config else None
        self.prepared = True

    def flush(self):
        if not self.prepared:
            raise RuntimeError("State must be restored before it can be published")
        if not self.config:
            return
        digest = state_sync._digest(self.home)
        if digest == self.digest:
            return
        data = state_sync._pack(self.home)
        if data is None:
            raise RuntimeError("State snapshot exceeds the size limit; changes were not saved")
        url, token = self.config
        with state_sync._request("PUT", url, token, body=data) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"State push failed: HTTP {response.status}")
        self.digest = digest

    async def run(self, stop: asyncio.Event):
        while True:
            try:
                await asyncio.wait_for(stop.wait(), state_sync.PUSH_INTERVAL_SECS)
            except TimeoutError:
                pass
            try:
                await asyncio.to_thread(self.flush)
            except Exception:
                logger.exception("[platform] state push failed; snapshot remains pending")
                if stop.is_set():
                    raise
            if stop.is_set():
                return


async def _stop_child(child):
    if child is None or child.returncode is not None:
        return
    try:
        child.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(child.wait(), 15)
    except TimeoutError:
        logger.warning("[platform] Agent did not stop within grace period; terminating")
        try:
            child.kill()
        except ProcessLookupError:
            pass
        await child.wait()


async def serve(service, *, log_level="INFO", state_home=None,
                agent_command=None, stop=None):
    """Serve until platform exit or explicit shutdown, regardless of Agent exit.

    A cooperating-process lock prevents two platform snapshot publishers on the
    same filesystem. It is not a distributed fencing mechanism (migration P5).
    """
    stop = stop if stop is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            signals.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    home = Path(state_home or os.environ.get("PANTHEON_STATE_HOME")
                or service.workspace_path).resolve()
    child = None
    worker = sync_task = stop_task = child_wait = None
    sync_stop = asyncio.Event()
    try:
        snapshot = SnapshotSession(home)
        with ExitStack() as locks:
            if snapshot.config:
                # Acquire once for the entire process lifetime, before restore.
                lock = registry_lock(home / ".pantheon/platform-state-sync.lock", timeout=0)
                locks.enter_context(lock)
            await asyncio.to_thread(snapshot.prepare)
            sync_task = asyncio.create_task(snapshot.run(sync_stop))
            try:
                worker = asyncio.create_task(service.run(log_level=log_level))
                stop_task = asyncio.create_task(stop.wait())
                if agent_command and not stop.is_set():
                    try:
                        child = await asyncio.create_subprocess_exec(
                            *agent_command, env=agent_environment())
                        child_wait = asyncio.create_task(child.wait())
                    except OSError:
                        logger.exception("[platform] Agent launch failed; platform remains available")
                watched = {worker, stop_task}
                if child_wait:
                    watched.add(child_wait)
                while True:
                    done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                    if worker in done:
                        await worker  # Surface fatal platform failure to the supervisor.
                        break
                    if stop_task in done:
                        break
                    if child_wait in done:
                        logger.warning("[platform] Agent exited ({}); platform remains available",
                                       child.returncode)
                        watched.remove(child_wait)
            finally:
                # Stop writers before the final snapshot, including Agent cleanup.
                cleanup_error = None
                try:
                    await _stop_child(child)
                    if worker is not None:
                        worker.cancel()
                        result, = await asyncio.gather(worker, return_exceptions=True)
                        if isinstance(result, Exception):
                            cleanup_error = result
                finally:
                    sync_stop.set()
                    try:
                        await sync_task
                    finally:
                        for task in (stop_task, child_wait):
                            if task is not None:
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                if cleanup_error is not None:
                    raise cleanup_error
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)


def legacy_agent_command(identity: str, args: list[str]) -> list[str]:
    return [sys.executable, "-m", "pantheon.chatroom", "--id_hash=" + identity, *args]
