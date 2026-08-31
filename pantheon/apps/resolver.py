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


class NotJoinedError(RuntimeError):
    """The local runner has not joined the fleet yet — wait, don't trip."""


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
        self._tmp_creds: str | None = None
        self._started: dict[tuple[str, str], str] = {}  # (service_type, scope) -> service_id
        self._nodes_cache: tuple[float, list[dict]] | None = None  # (monotonic, records)
        # Circuit breaker: when the fleet path is wired but not actually
        # reachable (wrong creds, runner down), every bind would otherwise
        # pay the request timeout before falling back. After a few
        # consecutive failures the resolver takes itself out of the path —
        # but only for a cooldown: the body (the runner) joining late is a
        # designed state, and the brain must pick it back up when it comes.
        self._consecutive_failures = 0
        self._disabled_at: float | None = None

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
            raise NotJoinedError(
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
    #: Seconds the breaker stays open before letting one probe through.
    COOLDOWN_S = 30.0

    def resolves(self, service_type: str) -> bool:
        """Whether this toolset is a catalog App this resolver can serve.

        Deliberately NOT breaker-gated: instances already running keep
        answering while the fleet path heals — the kill-test showed the
        old whole-face lock-out also took down the live python instance,
        which was the only hand that could restart the runner.
        """
        from pantheon.apps.registry import by_service_type

        return service_type in by_service_type()

    def _gate_new_starts(self) -> None:
        """The breaker, applied where it belongs: NEW instance starts.

        Cache hits bypass it entirely. When open, waits out the cooldown;
        then half-opens — one start attempt gets through, its failure
        re-opens immediately, its success closes.
        """
        if self._disabled_at is None:
            return
        import time as _t

        if _t.monotonic() - self._disabled_at < self.COOLDOWN_S:
            raise RuntimeError(
                "fleet App path is cooling down after repeated failures; "
                f"retrying within {self.COOLDOWN_S:.0f}s")
        self._disabled_at = None
        self._consecutive_failures = self.MAX_FAILURES - 1
        logger.info("[apps] breaker half-open — probing the fleet path again")

    def _note_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.MAX_FAILURES and self._disabled_at is None:
            import time as _t

            self._disabled_at = _t.monotonic()
            logger.warning(
                f"[apps] fleet App-instance path disabled after "
                f"{self._consecutive_failures} consecutive failures; "
                f"retrying in {self.COOLDOWN_S:.0f}s"
            )

    async def _ensure_client(self):
        if self._nc is not None and not self._nc.is_connected:
            # Short-lived creds lapsed or the link dropped; nats-py won't
            # re-read a creds file. Drop everything and rebuild below.
            try:
                await self._nc.close()
            except Exception:
                pass
            self._nc = None
            self._client = None
            self._drop_tmp_creds()
        if self._client is None:
            import nats

            from pantheon.apps.client import AppClient

            connect_kwargs: dict = {
                "connect_timeout": 5,
                "name": "pantheon-apps-resolver",
            }
            controller = os.environ.get("FLEET_CONTROLLER_URL")
            key = os.environ.get("FLEET_KEY")
            if controller and key:
                # USER-scope join: the controller mints creds whose JWT
                # allows publishing fleet.<fid>.> — commanding one's own
                # nodes is the product semantics, and this resolver IS the
                # user's binding hand. (Node creds deliberately cannot
                # publish cmd subjects — even their own.)
                import httpx

                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(
                        controller.rstrip("/") + "/join", json={"key": key}
                    )
                    r.raise_for_status()
                    data = r.json()
                self._fleet = data["fleet_id"]
                connect_kwargs["servers"] = [data["nats_url"]]
                if data.get("creds"):
                    import tempfile

                    tf = tempfile.NamedTemporaryFile(
                        "w", suffix=".creds", delete=False
                    )
                    tf.write(data["creds"])
                    tf.close()
                    os.chmod(tf.name, 0o600)
                    self._tmp_creds = tf.name
                    connect_kwargs["user_credentials"] = tf.name
                    # Match the JWT's per-fleet inbox scope (_INBOX_<fid>.>).
                    connect_kwargs["inbox_prefix"] = b"_INBOX_" + self._fleet.encode()
            else:
                # Dev: the local runner's own NATS (unauthenticated).
                servers = os.environ.get(
                    "NATS_SERVERS", "nats://localhost:4222"
                ).split("|")
                if self._state_dir:
                    try:
                        info = json.loads(
                            (Path(self._state_dir) / "runtime.json").read_text()
                        )
                        if info.get("nats_url"):
                            servers = [info["nats_url"]]
                    except Exception:
                        pass
                connect_kwargs["servers"] = servers
            self._nc = await nats.connect(**connect_kwargs)
            self._client = AppClient(self._nc, self._fleet)
        return self._client

    def _drop_tmp_creds(self) -> None:
        if getattr(self, "_tmp_creds", None):
            try:
                os.remove(self._tmp_creds)
            except OSError:
                pass
            self._tmp_creds = None

    @staticmethod
    def project_scope(project_dir: str) -> str:
        """The instance scope key for one project directory (§04: scope=project).

        Deterministic across processes — every worker maps the same project to
        the same instance.
        """
        import hashlib

        h = hashlib.sha256(str(Path(project_dir).resolve()).encode()).hexdigest()
        return f"proj{h[:10]}"

    # ── placement (P5) ──────────────────────────────────────────────────

    async def _list_nodes(self) -> list[dict]:
        """The fleet's node records, from the registry KV (10s cache).

        Same read the fleet toolset does: an ordered LAST_PER_SUBJECT drain
        with the stream named explicitly, so scoped credentials never need
        $JS.API.STREAM.NAMES. Any failure returns [] — placement must
        degrade to "local node", never take the bind path down.
        """
        import time as _t

        if self._nodes_cache and _t.monotonic() - self._nodes_cache[0] < 10:
            return self._nodes_cache[1]
        records: list[dict] = []
        try:
            await self._ensure_client()
            from nats.js import api

            js = self._nc.jetstream()
            bucket = f"FLEET_{self._fleet}_NODES"
            sub = await js.subscribe(
                f"$KV.{bucket}.>",
                stream=f"KV_{bucket}",
                ordered_consumer=True,
                deliver_policy=api.DeliverPolicy.LAST_PER_SUBJECT,
            )
            by_id: dict[str, dict] = {}
            try:
                while True:
                    try:
                        msg = await sub.next_msg(timeout=1.0)
                    except Exception:
                        break
                    if (msg.headers or {}).get("KV-Operation") in ("DEL", "PURGE"):
                        continue
                    try:
                        rec = json.loads(msg.data)
                    except Exception:
                        continue
                    nid = rec.get("node_id")
                    if nid:
                        by_id[nid] = rec
            finally:
                try:
                    await sub.unsubscribe()
                except Exception:
                    pass
            records = list(by_id.values())
        except Exception as e:
            logger.debug(f"[apps] node listing unavailable ({e}); placing locally")
        self._nodes_cache = (_t.monotonic(), records)
        return records

    async def _place(self, app) -> str:
        """Pick the node for an App instance (placement.requires × caps).

        Local node first — it is where the user's files are; a remote node
        only wins when the local one cannot host the App at all. `prefer`
        breaks ties among remote candidates by kind or label. No candidate
        (or no registry) falls back to local: a single-node fleet must
        behave exactly as before this existed.
        """
        requires = list(app.manifest.placement.requires)
        prefer = list(app.manifest.placement.prefer)
        if not requires:
            return self._node
        nodes = await self._list_nodes()
        need_python = app.manifest.runtime.value == "process"

        def caps(n: dict) -> set:
            return set((n.get("capability") or {}).get("caps") or [])

        def runtimes(n: dict) -> dict:
            return (n.get("capability") or {}).get("runtimes") or {}

        fits = [n for n in nodes
                if set(requires) <= caps(n)
                and (not need_python or "python" in runtimes(n))]
        if not fits:
            if nodes:
                logger.warning(
                    f"[apps] no node fits {app.manifest.id} "
                    f"(requires {requires}); trying the local node")
            return self._node
        for n in fits:
            if n.get("node_id") == self._node:
                return self._node
        if prefer:
            for n in fits:
                if n.get("kind") in prefer or set(prefer) & set(n.get("labels") or []):
                    return n["node_id"]
        chosen = fits[0]["node_id"]
        logger.info(
            f"[apps] placing {app.manifest.id} on node {chosen[:16]}… "
            f"(requires {requires}; local node lacks them)")
        return chosen

    def invalidate(self, service_type: str) -> None:
        """Forget cached instances of one type — the dead-body eraser.

        The ensure cache maps a toolset to a service_id forever; if the
        instance's process (or its whole runner) dies, every later call
        dials a subject nobody holds. Callers that exhaust the proxy's
        no-responders retries invalidate and re-ensure: the supervisor
        restarts what it still tracks, and a rejoined runner gets a fresh
        app_start.
        """
        for key in [k for k in self._started if k[0] == service_type]:
            del self._started[key]

    def started_instances(self, service_type: str) -> list[str]:
        """Service ids this resolver has ALREADY started for the type.

        A read of the cache — never starts anything. Metrics and idle
        cleanup ask "is a transfer running?", and booting an instance to
        answer that would be the tail wagging the dog.
        """
        return [sid for (st, _), sid in self._started.items() if st == service_type]

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
        self._gate_new_starts()
        try:
            self._ensure_coords()
            from pantheon.apps.registry import by_service_type
            from pantheon.apps.spec import apphost_spec

            app = by_service_type()[service_type]
            client = await self._ensure_client()
            target = await self._place(app)
            if not self._started:
                # First ensure: prove the node's cmd subject actually answers
                # before paying the longer app_start timeout — the fast "the
                # creds don't reach fleet subjects" detector.
                if not await client.ping(target, timeout=3.0):
                    raise RuntimeError(
                        f"node {target} does not answer on the fleet cmd "
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
            resp = await client.start(target, spec)
            if not resp.get("ok"):
                raise RuntimeError(f"app_start {app.manifest.id} on {target}: {resp}")
        except NotJoinedError:
            # The body has not arrived yet — a designed state while the
            # runner joins in the background, not a fault. Counting it
            # toward the breaker made an early-asking brain lose its body
            # permanently.
            raise
        except Exception:
            self._note_failure()
            raise
        self._consecutive_failures = 0
        self._disabled_at = None
        self._started[key] = spec["service_id"]
        logger.info(
            f"[apps] {service_type} -> app {app.manifest.id} instance "
            f"{spec['service_id'][:12]}… scope={scope} on node {target}"
        )
        return spec["service_id"]

    async def close(self):
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._client = None
        self._drop_tmp_creds()


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
