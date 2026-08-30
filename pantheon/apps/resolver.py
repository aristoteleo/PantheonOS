"""AppInstanceResolver — a toolset name resolved to a live App instance (P3).

The endpoint-free path: given the service names templates already use
('shell', 'file_manager'), ensure an instance is running on this user's node
via the fleet supervisor and hand back the service_id to dial directly
(ToolsetProxy.from_toolset — the mode that has existed all along).

The resolver is the ONLY binding path — the endpoint it once fell back to
is gone. Coordinates come from the environment:

    PANTHEON_FLEET_ID=<fleet id>          (the user's fleet)
    PANTHEON_FLEET_NODE_ID=<node id>      (the node to place on — own sandbox)
    PANTHEON_USER_SEED=<id_hash>          (instance service-id seeds)

In a sandbox the local runner boots asynchronously and generates its own
node id, so the id/fleet cannot be exported ahead of it. The runner writes
<state-dir>/runtime.json ({node_id, fleet_id, nats_url}) once joined; when
the explicit coordinates are absent, the resolver reads that file lazily at
first use instead:

    PANTHEON_FLEET_STATE_DIR=/tmp/fleet-node   (the runner's --state-dir)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pantheon.utils.log import logger


class AppInstanceResolver:
    """Ensure-and-dial App instances for this user on one node."""

    def __init__(
        self,
        fleet_id: str,
        node_id: str,
        user_seed: str,
        workdir: str,
        state_dir: str | None = None,
    ):
        self._fleet = fleet_id
        self._node = node_id
        self._seed = user_seed
        self._workdir = workdir
        self._state_dir = state_dir  # lazy runtime.json source when ids empty
        self._nc = None
        self._client = None
        self._started: dict[tuple[str, str], str] = {}  # (service_type, scope) -> service_id
        # Circuit breaker: when the fleet path is wired but not actually
        # reachable (wrong creds, runner down), every bind would otherwise
        # pay the request timeout before falling back. After a few
        # consecutive failures the resolver takes itself out of the path.
        self._consecutive_failures = 0
        self._disabled = False

    @classmethod
    def from_env(cls, workdir: str | None = None) -> "AppInstanceResolver | None":
        """The environment-configured resolver, or None when not wired."""
        fleet_id = os.environ.get("PANTHEON_FLEET_ID", "")
        node_id = os.environ.get("PANTHEON_FLEET_NODE_ID", "")
        seed = os.environ.get("PANTHEON_USER_SEED") or os.environ.get("ID_HASH", "")
        state_dir = os.environ.get("PANTHEON_FLEET_STATE_DIR", "/tmp/fleet-node")
        if not seed:
            logger.warning(
                "[apps] no user seed (PANTHEON_USER_SEED / ID_HASH); "
                "App instances unavailable"
            )
            return None
        if not (fleet_id and node_id):
            # The local runner's runtime.json fills these in lazily — it may
            # not exist yet (the runner boots in the background).
            return cls(fleet_id, node_id, seed, workdir or os.getcwd(),
                       state_dir=state_dir)
        return cls(fleet_id, node_id, seed, workdir or os.getcwd())

    def _ensure_coords(self) -> None:
        """Fill fleet/node ids from the runner's runtime.json when deferred."""
        if self._fleet and self._node:
            return
        path = Path(self._state_dir or "") / "runtime.json"
        try:
            info = json.loads(path.read_text())
        except FileNotFoundError:
            raise RuntimeError(
                f"fleet runner not joined yet ({path} missing)"
            ) from None
        except Exception as e:
            raise RuntimeError(f"unreadable {path}: {e}") from None
        self._fleet = self._fleet or info.get("fleet_id", "")
        self._node = self._node or info.get("node_id", "")
        if not (self._fleet and self._node):
            raise RuntimeError(f"incomplete coordinates in {path}: {info}")

    #: Consecutive ensure failures before the resolver disables itself.
    MAX_FAILURES = 3

    def resolves(self, service_type: str) -> bool:
        """Whether this resolver can serve the named toolset as an App."""
        if self._disabled:
            return False
        from pantheon.apps.registry import by_service_type

        return service_type in by_service_type()

    def _note_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.MAX_FAILURES and not self._disabled:
            self._disabled = True
            logger.warning(
                f"[apps] fleet App-instance path disabled after "
                f"{self._consecutive_failures} consecutive failures; "
                f"tool binds will fail until the runner is reachable"
            )

    async def _ensure_client(self):
        if self._client is None:
            import nats

            from pantheon.apps.client import AppClient

            # The fleet control plane may live on a different NATS (and
            # behind different auth) than the worker's own bus. The runner
            # already joined it — reuse ITS coordinates and credentials from
            # the shared state dir (same container, same trust domain):
            # runtime.json carries nats_url, fleet.creds the scoped JWT the
            # controller minted for this node. Dev runners have neither and
            # fall back to NATS_SERVERS unauthenticated.
            servers = os.environ.get("NATS_SERVERS", "nats://localhost:4222").split("|")
            creds: str | None = None
            if self._state_dir:
                state = Path(self._state_dir)
                try:
                    info = json.loads((state / "runtime.json").read_text())
                    if info.get("nats_url"):
                        servers = [info["nats_url"]]
                except Exception:
                    pass
                creds_path = state / "fleet.creds"
                if creds_path.is_file():
                    creds = str(creds_path)
            connect_kwargs: dict = {"servers": servers, "connect_timeout": 5}
            if creds:
                connect_kwargs["user_credentials"] = creds
                # Scoped fleet creds only allow subscriptions under the
                # fleet's own inbox namespace (the runner connects with
                # CustomInboxPrefix("_INBOX_"+fleet)) — requests made with
                # the default _INBOX prefix would never see their replies.
                connect_kwargs["inbox_prefix"] = f"_INBOX_{self._fleet}"
            self._nc = await nats.connect(**connect_kwargs)
            self._client = AppClient(self._nc, self._fleet)
        return self._client

    @staticmethod
    def project_scope(project_dir: str) -> str:
        """The instance scope key for one project directory (§04: scope=project).

        Deterministic across processes — every worker maps the same project to
        the same instance.
        """
        import hashlib

        h = hashlib.sha256(str(Path(project_dir).resolve()).encode()).hexdigest()
        return f"proj{h[:10]}"

    async def ensure_instance(
        self,
        service_type: str,
        *,
        scope: str = "app",
        workdir: str | None = None,
    ) -> str:
        """Start (idempotently) and return the instance's service_id.

        scope="app" is the user-wide default instance; a project-scoped call
        (scope=project_scope(dir), workdir=dir) gets its OWN instance rooted
        in that project — per-project isolation by separate processes rather
        than per-call cwd steering.
        """
        key = (service_type, scope)
        if key in self._started:
            return self._started[key]
        try:
            self._ensure_coords()
            from pantheon.apps.registry import by_service_type
            from pantheon.apps.spec import apphost_spec

            app = by_service_type()[service_type]
            client = await self._ensure_client()
            if not self._started:
                # First ensure: prove the node's cmd subject actually answers
                # before paying the longer app_start timeout — the fast "the
                # creds don't reach fleet subjects" detector.
                if not await client.ping(self._node, timeout=3.0):
                    raise RuntimeError(
                        f"node {self._node} does not answer on the fleet cmd "
                        f"subject (creds/scope?)"
                    )
            spec = apphost_spec(
                app.manifest.id,
                user_seed=self._seed,
                workdir=workdir or self._workdir,
                scope=scope,
                env={k: v for k, v in os.environ.items()
                     if k.startswith("NATS_") or k in ("PYTHONPATH", "PATH")},
            )
            resp = await client.start(self._node, spec)
            if not resp.get("ok"):
                raise RuntimeError(f"app_start {app.manifest.id} on {self._node}: {resp}")
        except Exception:
            self._note_failure()
            raise
        self._consecutive_failures = 0
        self._started[key] = spec["service_id"]
        logger.info(
            f"[apps] {service_type} -> app {app.manifest.id} instance "
            f"{spec['service_id'][:12]}… scope={scope} on node {self._node}"
        )
        return spec["service_id"]

    async def close(self):
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._client = None


# ---- process-wide shared resolver ------------------------------------------
_shared: AppInstanceResolver | None = None
_shared_built = False


def get_shared_resolver(workdir: str | None = None) -> AppInstanceResolver | None:
    """The one resolver every binding site shares (factory, ChatRoom proxy).

    Built lazily from the environment on first ask; None means the flag is
    off/unwired and callers take the endpoint route exactly as before.
    """
    global _shared, _shared_built
    if not _shared_built:
        _shared_built = True
        _shared = AppInstanceResolver.from_env(workdir=workdir)
        if _shared is not None:
            logger.info("[apps] fleet App-instance binding is ON")
    return _shared


def reset_shared_resolver() -> None:
    """Testing hook: forget the cached shared resolver."""
    global _shared, _shared_built
    _shared = None
    _shared_built = False
