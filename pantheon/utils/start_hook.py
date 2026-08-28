"""The user's on-start hook, and who has to wait for it.

The hook lives on the Volume and runs on every boot, because the image's
system paths are ephemeral: an `apt-get install` from the last session is
gone, so the instructions for putting it back are what persist, not the
result (see docker-entrypoint-dual-mode.sh).

Running it before the agent made "the hook has finished before the agent
starts" true for everyone, at the cost of putting it on the boot's
critical path — four seconds of a nine-second boot, in a real sandbox,
re-installing a tool that was already there. But the guarantee only
matters to the caller who USES what the hook installs. So the hook runs
alongside the boot now, and the wait moved to the two places that can
actually observe it: running a shell command, and running code.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from pantheon.utils.log import logger

# Written by the entrypoint when the hook finishes (or fails, or times out).
DONE_MARKER = Path("/tmp/pantheon-on-start.done")
# Written by the entrypoint when it starts a hook in the background. Absent
# means there is nothing to wait for — no hook, or an older entrypoint that
# already ran it to completion before starting us.
RUNNING_MARKER = Path("/tmp/pantheon-on-start.running")

# Long enough for an apt install on a cold mirror, bounded so a hook that
# hangs cannot make the agent look hung too. The entrypoint's own timeout
# (PANTHEON_SETUP_TIMEOUT, 300s default) is the real ceiling.
DEFAULT_WAIT_S = float(os.environ.get("PANTHEON_SETUP_WAIT_S", "120"))

_warned = False


async def wait_for_start_hook(timeout_s: float | None = None) -> None:
    """Block until the boot hook has finished, if one is still running.

    Costs nothing in the normal case: by the time anybody asks the agent to
    do something, a four-second hook is long done and this is two stat
    calls.
    """
    global _warned
    if not RUNNING_MARKER.exists() or DONE_MARKER.exists():
        return
    deadline = time.monotonic() + (timeout_s if timeout_s is not None else DEFAULT_WAIT_S)
    if not _warned:
        _warned = True
        logger.info("waiting for the workspace's on-start hook to finish")
    while time.monotonic() < deadline:
        if DONE_MARKER.exists():
            return
        await asyncio.sleep(0.1)
    logger.warning(
        "the on-start hook has not finished; continuing without it — "
        "anything it installs may be missing"
    )
