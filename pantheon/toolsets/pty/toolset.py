"""PtyToolSet — a real terminal in the sandbox.

The `shell` toolset runs commands behind pipes with an end-of-command marker.
That is right for an agent, which wants one command and its complete output,
but it is not a terminal: with no tty, bash never enters readline, so a UI gets
no prompt, no tab completion, no job control, and cannot run vim, top or less
at all. Output also only arrives when the command finishes.

This toolset allocates a pseudo-terminal instead. A shell runs in its own
session with the pty slave as its stdin, stdout and stderr, so it behaves
exactly as it does over ssh, and everything it writes is streamed out as it
appears rather than at the end.

The pty is watched by the event loop itself (``add_reader``) rather than by a
blocking read in a thread, because that thread would come from the default
executor — the same pool ``run_python_code`` submits to.

Transport: bytes are base64-encoded and published on the NATS stream
``pty_<session_id>``, which a frontend subscribes to directly. Base64 rather
than raw text because a pty carries arbitrary bytes — a half-read UTF-8
sequence split across two reads is normal, and JSON cannot hold it.
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import os
import pty
import shutil
import signal
import struct
import termios
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

# How much to read from the pty at a time. A terminal produces bursts (a
# `find /` scrolling past), and larger reads mean fewer NATS messages.
READ_CHUNK = 65536

# Shells to try, best first.
SHELL_CANDIDATES = ("/bin/bash", "/usr/bin/bash", "/bin/zsh", "/bin/sh")

# A session with no reader and no writer eventually costs the pod a process.
IDLE_REAP_SECONDS = 60 * 60

# How long pty_open waits for the shell's first output — the prompt — so it can
# return it rather than publishing it to a stream nobody is subscribed to yet.
# It stops early the moment the shell goes quiet, so this is a ceiling, not a
# cost: a bash prompt is typically ready well inside it.
PROMPT_GRACE_SECONDS = 0.25
PROMPT_POLL_SECONDS = 0.01


def _pick_shell() -> str:
    for candidate in SHELL_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("sh") or "/bin/sh"


@dataclass
class PtySession:
    """One pseudo-terminal and the shell running on it."""

    session_id: str
    master_fd: int
    process: asyncio.subprocess.Process
    cols: int
    rows: int
    cwd: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    reader_task: asyncio.Task | None = None
    #: Bytes from the pty, awaiting publication. `None` marks end of stream.
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    exited: bool = False
    exit_code: int | None = None

    @property
    def stream_id(self) -> str:
        return f"pty_{self.session_id}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stream_id": self.stream_id,
            "cols": self.cols,
            "rows": self.rows,
            "cwd": self.cwd,
            "pid": self.process.pid,
            "exited": self.exited,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
        }


class PtyToolSet(ToolSet):
    """Pseudo-terminal sessions for a terminal UI.

    Every method is UI-facing (``@tool(exclude=True)``): an agent has no use
    for a byte stream it would have to render, and `shell` already gives it the
    command-and-output shape it wants.
    """

    def __init__(self, name: str = "pty", workdir: str | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self._sessions: dict[str, PtySession] = {}
        self._backend = None
        self._workdir = workdir

    # ── streaming ─────────────────────────────────────────────────────────

    async def _get_backend(self):
        if self._backend is None:
            from pantheon.remote import RemoteBackendFactory

            self._backend = RemoteBackendFactory.create_backend()
        return self._backend

    async def _publish(self, session: PtySession, payload: dict[str, Any]) -> None:
        """Send one event on this session's stream. Best effort throughout:
        a terminal that loses a frame is survivable, one that raises into the
        read loop is not."""
        from pantheon.remote.backend.base import StreamMessage, StreamType

        try:
            backend = await self._get_backend()
            channel = await backend.get_or_create_stream(
                session.stream_id, StreamType.CUSTOM,
            )
            await channel.publish(
                StreamMessage(
                    type=StreamType.CUSTOM,
                    session_id=session.stream_id,
                    timestamp=time.time(),
                    data={**payload, "session_id": session.session_id},
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("pty: publish failed for {}: {}", session.session_id, e)

    def _attach_reader(self, session: PtySession) -> None:
        """Have the event loop watch the pty, rather than a thread block on it.

        The obvious implementation — a blocking ``os.read`` in
        ``run_in_executor(None, ...)`` — costs one thread-pool worker for the
        whole life of the session, and that pool is shared: the Python
        interpreter toolset submits every ``run_python_code`` call to the same
        default executor. A few open terminals would then throttle, and
        eventually starve, code execution. ``add_reader`` uses no thread at
        all, and output still leaves the instant it is produced.
        """
        loop = asyncio.get_running_loop()
        os.set_blocking(session.master_fd, False)
        loop.add_reader(session.master_fd, self._on_readable, session)

    def _detach_reader(self, session: PtySession) -> None:
        try:
            asyncio.get_running_loop().remove_reader(session.master_fd)
        except Exception:  # noqa: BLE001
            pass

    def _on_readable(self, session: PtySession) -> None:
        """Called by the loop when the shell has written something."""
        try:
            data = os.read(session.master_fd, READ_CHUNK)
        except BlockingIOError:
            return          # woken with nothing to take
        except OSError:
            data = b""      # EIO: the last slave closed, i.e. the shell is gone

        if not data:
            self._detach_reader(session)
            session.outbox.put_nowait(None)
            return

        session.last_active = time.time()
        # Queued rather than published here: this is a plain callback, so
        # publishing would mean spawning a task per read, and tasks finish in
        # whatever order the network allows. A terminal whose output arrives
        # out of order is worse than one that arrives late.
        session.outbox.put_nowait(data)

    async def _pump(self, session: PtySession) -> None:
        """Publish what the reader queued, in order."""
        while True:
            chunk = await session.outbox.get()
            if chunk is None:
                break
            # Drain whatever else arrived while that awaited: a burst — `find /`
            # scrolling past — becomes one message instead of dozens.
            parts = [chunk]
            ended = False
            while not session.outbox.empty():
                more = session.outbox.get_nowait()
                if more is None:
                    ended = True
                    break
                parts.append(more)
            await self._publish(
                session,
                {"type": "pty.data", "data": base64.b64encode(b"".join(parts)).decode()},
            )
            if ended:
                break

        # The shell is gone: report why, then let the session be collected.
        try:
            session.exit_code = await asyncio.wait_for(session.process.wait(), 5)
        except Exception:  # noqa: BLE001
            session.exit_code = None
        session.exited = True
        await self._publish(
            session, {"type": "pty.exit", "exit_code": session.exit_code},
        )
        logger.info(
            "pty: session {} exited (code={})", session.session_id, session.exit_code,
        )

        # A shell that quit on its own — someone typed `exit` — leaves its
        # session behind until the next pty_open reaps it, holding the master
        # fd all the while. Drop it here instead. A deliberate close has
        # already taken it out of the map, and `_terminate` owns the fd in that
        # case, so this does not race with it.
        if self._sessions.get(session.session_id) is session:
            self._sessions.pop(session.session_id, None)
            self._detach_reader(session)
            try:
                os.close(session.master_fd)
            except OSError:
                pass

    # ── UI-facing tools ───────────────────────────────────────────────────

    @tool(exclude=True)
    async def pty_open(
        self,
        cols: int = 80,
        rows: int = 24,
        cwd: str | None = None,
        shell: str | None = None,
    ) -> dict:
        """Start a shell on a new pseudo-terminal.

        Args:
            cols: Terminal width in columns.
            rows: Terminal height in rows.
            cwd: Working directory. Defaults to the toolset's workdir.
            shell: Shell to run. Defaults to bash where present.

        Returns:
            dict with success, session_id, stream_id — the NATS stream to
            subscribe to for output — and initial_output, base64 bytes the
            shell produced before the caller could possibly have subscribed.
            Write those to the terminal first, then attach to the stream.
        """
        self._reap_idle()

        target_cwd = cwd or self._workdir or os.getcwd()
        if not os.path.isdir(target_cwd):
            target_cwd = os.getcwd()

        master_fd, slave_fd = pty.openpty()
        _set_winsize(master_fd, rows, cols)

        env = os.environ.copy()
        # Without TERM, bash assumes a dumb terminal and drops colour, line
        # editing and cursor addressing — the very things a pty is for.
        env["TERM"] = env.get("TERM") or "xterm-256color"
        env["COLUMNS"] = str(cols)
        env["LINES"] = str(rows)

        shell_path = shell or _pick_shell()
        try:
            process = await asyncio.create_subprocess_exec(
                shell_path,
                "-i",
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=target_cwd,
                env=env,
                # Its own session, so the shell is the controlling process of
                # this pty and job control works — Ctrl-C reaches the
                # foreground job instead of the agent process.
                start_new_session=True,
            )
        except Exception as e:  # noqa: BLE001
            os.close(master_fd)
            os.close(slave_fd)
            return {"success": False, "error": f"could not start {shell_path}: {e}"}
        finally:
            # The parent must not hold the slave open, or the master never
            # sees EOF when the shell exits.
            try:
                os.close(slave_fd)
            except OSError:
                pass

        session_id = uuid.uuid4().hex[:12]
        session = PtySession(
            session_id=session_id,
            master_fd=master_fd,
            process=process,
            cols=cols,
            rows=rows,
            cwd=target_cwd,
        )
        self._sessions[session_id] = session
        self._attach_reader(session)

        # Hand back what the shell has already said.
        #
        # A caller cannot subscribe to the stream until it knows the stream id,
        # which it only learns from this return value — so its subscription is
        # always at least one round trip late, and the shell draws its prompt
        # within milliseconds of the pty existing. NATS core pub/sub has no
        # replay, so that prompt is published to nobody and lost: verified
        # against a real pod, a caller subscribing the instant pty_open
        # returned still received zero bytes, and the terminal came up blank
        # with a shell running perfectly well behind it.
        #
        # The pump does not start until this has drained the queue, so nothing
        # is duplicated and nothing is reordered — everything up to here comes
        # back in `initial_output`, everything after goes on the stream.
        # `shell.new_shell` returns its first output the same way.
        initial = await self._drain_initial(session)

        session.reader_task = asyncio.create_task(self._pump(session))

        logger.info(
            "pty: opened {} ({} {}x{}) in {}",
            session_id, shell_path, cols, rows, target_cwd,
        )
        return {
            "success": True,
            "initial_output": base64.b64encode(initial).decode(),
            **session.snapshot(),
        }

    async def _drain_initial(self, session: PtySession) -> bytes:
        """Whatever the shell writes in its first moments, before the pump runs.

        Bounded twice: it stops as soon as the shell goes quiet, and never
        waits longer than PROMPT_GRACE_SECONDS in total, so a shell that says
        nothing costs one short sleep rather than a stalled open.
        """
        deadline = time.monotonic() + PROMPT_GRACE_SECONDS
        parts: list[bytes] = []
        while time.monotonic() < deadline:
            await asyncio.sleep(PROMPT_POLL_SECONDS)
            if session.outbox.empty():
                # Something already arrived and the shell has gone quiet: a
                # prompt is drawn in one go, so there is no reason to keep
                # waiting out the grace period.
                if parts:
                    break
                continue
            while not session.outbox.empty():
                chunk = session.outbox.get_nowait()
                if chunk is None:
                    # The shell exited immediately. Put the sentinel back so
                    # the pump still reports the exit.
                    session.outbox.put_nowait(None)
                    return b"".join(parts)
                parts.append(chunk)
        return b"".join(parts)

    @tool(exclude=True)
    async def pty_write(self, session_id: str, data: str) -> dict:
        """Send keystrokes to a session.

        Args:
            session_id: id from pty_open.
            data: base64-encoded bytes, exactly as the terminal produced them
                (control characters and escape sequences included).
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"success": False, "error": f"no pty session '{session_id}'"}
        if session.exited:
            return {"success": False, "error": "session has exited"}

        try:
            raw = base64.b64decode(data)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"data is not base64: {e}"}

        try:
            os.write(session.master_fd, raw)
        except OSError as e:
            return {"success": False, "error": str(e)}
        session.last_active = time.time()
        return {"success": True}

    @tool(exclude=True)
    async def pty_resize(self, session_id: str, cols: int, rows: int) -> dict:
        """Tell the shell the window changed size.

        Without this the shell keeps wrapping at the old width, so a resized
        window redraws its prompt in the wrong place.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"success": False, "error": f"no pty session '{session_id}'"}
        try:
            _set_winsize(session.master_fd, rows, cols)
            # SIGWINCH is what makes a running full-screen program redraw.
            os.killpg(os.getpgid(session.process.pid), signal.SIGWINCH)
        except Exception as e:  # noqa: BLE001
            logger.debug("pty: resize {}: {}", session_id, e)
        session.cols, session.rows = cols, rows
        return {"success": True, "cols": cols, "rows": rows}

    @tool(exclude=True)
    async def pty_close(self, session_id: str) -> dict:
        """End a session and reap its shell."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return {"success": False, "error": f"no pty session '{session_id}'"}
        await self._terminate(session)
        return {"success": True}

    @tool(exclude=True)
    async def pty_list(self) -> dict:
        """Sessions this toolset is holding."""
        return {
            "success": True,
            "sessions": [s.snapshot() for s in self._sessions.values()],
        }

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def _terminate(self, session: PtySession) -> None:
        try:
            # Signal the group: the shell may have children of its own.
            os.killpg(os.getpgid(session.process.pid), signal.SIGHUP)
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(session.process.wait(), 3)
        except Exception:  # noqa: BLE001
            try:
                session.process.kill()
            except Exception:  # noqa: BLE001
                pass
        # Let the pump end on its own so it still publishes `pty.exit`: a UI
        # that closed one tab of several is watching this stream to know the
        # session is really gone. Cancelling here outright meant the event was
        # never sent for a deliberate close, only for a shell that quit itself.
        self._detach_reader(session)
        session.outbox.put_nowait(None)
        if session.reader_task:
            try:
                await asyncio.wait_for(asyncio.shield(session.reader_task), 3)
            except Exception:  # noqa: BLE001
                session.reader_task.cancel()
        try:
            os.close(session.master_fd)
        except OSError:
            pass
        logger.info("pty: closed {}", session.session_id)

    def _reap_idle(self) -> None:
        """Drop sessions whose shell has exited or that nobody has touched.

        A browser tab that closes without calling pty_close leaves a shell
        running; without this, they accumulate for the life of the sandbox.
        """
        cutoff = time.time() - IDLE_REAP_SECONDS
        for sid, session in list(self._sessions.items()):
            if session.exited or session.last_active < cutoff:
                self._sessions.pop(sid, None)
                asyncio.create_task(self._terminate(session))

    async def cleanup(self) -> None:
        for session in list(self._sessions.values()):
            await self._terminate(session)
        self._sessions.clear()


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    """TIOCSWINSZ — how a terminal's size is communicated to the kernel."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
