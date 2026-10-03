"""Fleet management RPCs independent of Agent execution.

ChatRoom temporarily inherits this API for wire compatibility. The platform
service serves the same implementation without loading ChatRoom.
"""

import asyncio

from pantheon.toolset import tool
from pantheon.utils.log import logger


class FleetAPI:
    def _resource_session_owner(self):
        from pantheon.apps.resource_sessions import ResourceSessionOwner
        starter = self._dependency_starter()
        if starter is None:
            return None
        return ResourceSessionOwner(starter.lifecycle, starter.root.parent / 'app-resource-sessions')

    def _dependency_starter(self):
        from pathlib import Path
        import hashlib
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.lifecycle import FleetLifecycle
        from pantheon.apps.dependency_assembly import DependencyStarter
        resolver = get_shared_resolver()
        if resolver is None:
            return None
        namespace = hashlib.sha256(resolver._seed.encode()).hexdigest()
        root = Path.home() / '.pantheon' / 'platform-private' / namespace / 'app-dependency-starts'
        return DependencyStarter(FleetLifecycle(resolver), root)

    def _start_dependency_maintenance(self):
        task = getattr(self, '_dependency_maintenance_task', None)
        if task is not None and not task.done():
            self._dependency_maintenance_wake.set()
            self._resource_session_maintenance_wake.set()
            return
        wake = self._dependency_maintenance_wake = asyncio.Event()
        session_wake = self._resource_session_maintenance_wake = asyncio.Event()

        async def maintain(factory, signal, status_attribute):
            while True:
                signal.clear()
                try:
                    coordinator = factory()
                    if coordinator is not None:
                        summary = await coordinator.reconcile_once()
                        setattr(self, status_attribute, summary)
                        if summary.get('expired') or summary.get('invalid') or summary.get('lost'):
                            logger.warning('App owner maintenance requires attention: {}', summary)
                except Exception:
                    # Node/Hub outages do not prove termination or authorize
                    # new grants. Existing grants expire if maintenance cannot
                    # re-establish their exact live identities in time.
                    setattr(self, status_attribute, {'deferred': 1})
                try:
                    await asyncio.wait_for(signal.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass

        async def owners():
            # Slow/unavailable grant authority must not hold session cleanup
            # or renewal behind it. Both loops belong to this platform lifetime.
            await asyncio.gather(
                maintain(self._dependency_starter, wake, '_dependency_maintenance_status'),
                maintain(self._resource_session_owner, session_wake, '_resource_session_maintenance_status'))

        self._dependency_maintenance_task = asyncio.create_task(owners())

    async def _stop_dependency_maintenance(self):
        task = getattr(self, '_dependency_maintenance_task', None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._dependency_maintenance_task = None
        # Do not revoke live App grants merely because this platform process
        # exits. A replacement owner resumes from receipts within the TTL.

    @tool
    async def fleet_inventory(self) -> dict:
        """Nodes and supervised App instances in the authenticated user's Fleet."""
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.builtin.fleet.inventory import fleet_inventory
        from pantheon.apps.builtin.fleet.update import published_release
        try:
            result = await fleet_inventory(get_shared_resolver())
            return {**result, 'fleet_release': await published_release()}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


    @tool
    async def fleet_app_lifecycle(self, node_id: str, action: str = 'status',
                                  digest: str = '', scope: str = 'app', generation: int = 0,
                                  operation_id: str = '', instance_id: str = '',
                                  revision: str = '', lease_id: str = '',
                                  release: bool = False, keep_alive: bool = False) -> dict:
        """Manage an installed App on one concrete Fleet node.

        status returns installations, instances, operation steps and errors.
        start/stop/uninstall/reconcile return an operation immediately; poll
        status for the result. Read generation from status before mutations.
        Reuse operation_id when a reply is lost. Stop preserves user data and
        can be blocked by pending saves. Uninstall requires stopped instances.
        Newer Runners inspect process liveness after restart; reconcile is
        also available explicitly and never replays interrupted hooks.
        This never starts on a different node or executes arbitrary commands.
        """
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.lifecycle import FleetLifecycle
        try:
            resolver = get_shared_resolver()
            if resolver is None:
                raise RuntimeError('Fleet is not connected')
            lifecycle = FleetLifecycle(resolver)
            if action == 'status':
                return {'success': True, **await lifecycle.status(node_id)}
            if action in {'lease', 'keep_alive'}:
                return {'success': True, **await lifecycle.usage(node_id, action,
                    instance_id=instance_id, revision=revision, generation=generation,
                    lease_id=lease_id, release=release, keep_alive=keep_alive)}
            operation = await lifecycle.submit(node_id, action, digest, scope=scope,
                generation=generation, operation_id=operation_id or None)
            return {'success': True, 'operation': operation}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


    @tool(exclude=True)
    async def fleet_app_resource_session(self, action: str, consumer: dict,
                                         operation_id: str, owner_ref: str = '',
                                         provider: dict | None = None, app_id: str = '',
                                         kind: str = '', preparation_id: str = '') -> dict:
        """Owner-only resource-session acquisition, inspection and release.

        Use a stable logical resource owner, not an Agent configuration name.
        consumer pins the running generation (or the next generation of an exact
        preparation). A receipt is not a tool permission. The dependency grant
        must separately bind its session ID before a consumer can use it.
        """
        from pantheon.apps.dependency_assembly import AssemblyError
        try:
            owner = self._resource_session_owner()
            if owner is None:
                raise AssemblyError('Fleet is not connected')
            if action == 'acquire':
                record = await owner.acquire(consumer=consumer, operation_id=operation_id,
                    owner_ref=owner_ref, provider=provider, app_id=app_id, kind=kind,
                    preparation_id=preparation_id)
            elif action in {'release', 'status'}:
                if owner_ref or provider is not None or app_id or kind or preparation_id:
                    raise AssemblyError('Inspect or release with only the original consumer and operation ID')
                method = owner.release if action == 'release' else owner.inspect
                record = await method(consumer=consumer, operation_id=operation_id)
            else:
                raise AssemblyError('Unsupported resource-session operation')
            self._start_dependency_maintenance()
            return {'success': True, 'session': record}
        except AssemblyError as exc:
            return {'success': False, 'error': str(exc)}
        except Exception:
            return {'success': False, 'error': 'Resource session outcome is unknown; retry the original operation or inspect Fleet status'}

    @tool(exclude=True)
    async def fleet_app_start_dependencies(self, consumer: dict, preparation_id: str,
                                          operation_id: str, bindings: dict,
                                          components: dict) -> dict:
        """Bind declared providers, privately configure, then start a prepared App.

        Owner control-plane API, not an App tool. Reuse the complete recipe and
        operation ID after a lost reply. Providers must already be installed and
        ready; no provider/node fallback or session creation is implied. The
        platform maintains the same grants while their exact consumers live.
        """
        from pantheon.apps.dependency_assembly import AssemblyError
        try:
            starter = self._dependency_starter()
            if starter is None:
                raise AssemblyError('Fleet is not connected')
            result = await starter.start(consumer=consumer, preparation_id=preparation_id,
                operation_id=operation_id, bindings=bindings, components=components)
            self._start_dependency_maintenance()
            return {'success': True, **result}
        except AssemblyError as exc:
            return {'success': False, 'error': str(exc)}
        except Exception:
            # Transport/JSON/filesystem exceptions can contain private config.
            return {'success': False, 'error': 'Dependency start was not acknowledged; retry the same operation and recipe or inspect Fleet status'}


    @tool
    async def fleet_update_nodes(self, node_ids: list[str] | None = None, tag: str = '') -> dict:
        """Update Fleet on the user's machine nodes to a release, then restart them.

        Empty node_ids = every machine node; empty tag = the release the
        Controller publishes. Each node downloads the release, verifies its
        checksum and restarts; a node with tasks, transfers or App operations
        in flight answers "deferred" and updates on its own once idle.
        Sandbox/pod nodes are updated with their image and are skipped.
        """
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.builtin.fleet.update import update_nodes
        try:
            return await update_nodes(get_shared_resolver(), node_ids, tag)
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


    @tool
    async def fleet_hpc(self, node_id: str, action: str = 'partitions', partition: str = '', cpus: int = 4,
                        mem_gb: int = 16, minutes: int = 240, gpus: int = 0, gpu_type: str = '', count: int = 1,
                        name: str = '', account: str = '', qos: str = '', job_id: str = '') -> dict:
        """Start and manage Fleet nodes on an HPC cluster through a Fleet node on its Slurm login node.

        partitions lists where jobs can run; launch submits `count` single-node
        jobs, each joining the Fleet as its own node (labels hpc, slurm-job:<id>)
        and leaving when the job ends; jobs lists them with Slurm state; cancel
        stops one (only jobs started from this login node). The HPC allocation
        is the user's own: launch only when the user asks.
        """
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.builtin.fleet import hpc
        try:
            resolver = get_shared_resolver()
            if action == 'launch':
                return await hpc.launch(resolver, node_id, partition=partition, cpus=cpus, mem_gb=mem_gb,
                                        minutes=minutes, gpus=gpus, gpu_type=gpu_type, count=count, name=name,
                                        account=account, qos=qos)
            if action == 'cancel':
                return {'success': True, **await hpc.call(resolver, node_id, 'cancel', job_id=job_id)}
            if action in ('partitions', 'jobs'):
                return {'success': True, **await hpc.call(resolver, node_id, action)}
            return {'success': False, 'error': 'action must be partitions, launch, jobs or cancel'}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


    @tool
    async def fleet_hpc_cluster(self, node_id: str, action: str = 'list', cluster_id: str = '',
                                cluster: dict | None = None, answer: dict | None = None, remember: bool = False,
                                request: dict | None = None, job_id: str = '') -> dict:
        """HPC clusters reached through a session the user signs in to (Fleet app).

        node_id is the machine that holds the SSH session. Sign-in answers arrive
        encrypted to that machine's per-prompt key; this backend cannot read them.
        """
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.builtin.fleet import hpc
        data = {'cluster_id': cluster_id, 'job_id': job_id, 'remember': remember}
        for key, value in (('cluster', cluster), ('answer', answer), ('request', request)):
            if value:
                data[key] = value
        try:
            return {'success': True, **await hpc.cluster(get_shared_resolver(), node_id, action, **data)}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


    @tool
    async def fleet_hpc_service(self, node_id: str, action: str = 'list', spec: dict | None = None,
                                instance_id: str = '', revision: str = '', generation: int = 0) -> dict:
        """Manage an HTTP service on an allocated HPC compute node.

        Start takes spec {name, argv, cwd, startup_seconds} and generation=1,
        or previous generation+1 after stop. argv is an argument array, not a
        shell string; ${HOST}, ${PORT}, ${WORKSPACE} are expanded on compute.
        The process must bind its assigned loopback HOST/PORT (also in env).
        Only one service runs per allocation. Files may be staged using
        hpc_workspace. Poll list for running/failed and bounded logs. Stop
        requires the exact instance_id, revision and generation from list.
        Work and access end with the allocation or attended SSH connection.
        """
        from pantheon.apps.resolver import get_shared_resolver
        from pantheon.apps.builtin.fleet import hpc
        try:
            data = {'instance_id': instance_id, 'revision': revision, 'generation': generation}
            if spec is not None:
                data['spec'] = spec
            return {'success': True, **await hpc.service(get_shared_resolver(), node_id, action, **data)}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


    async def _ensure_fleet_session_key(self) -> None:
        """Publish a session-derived ``FLEET_KEY`` (and start refreshing it) the
        first time a fleet tool runs, when the backend is logged in and no static
        ``pbk_`` key is set — so the local backend needn't hold a static bearer key.
        A no-op when a static key is configured (back-compat) or already handled."""
        if getattr(self, "_fleet_session_started", False):
            return
        self._fleet_session_started = True
        try:
            import asyncio

            from .fleet_session import fetch_fleet_session_key, use_session_cred

            if not use_session_cred():
                return  # static key present — nothing to fetch/refresh
            _key, ttl = await fetch_fleet_session_key()
            if _key and ttl > 0:
                self._fleet_session_task = asyncio.create_task(
                    self._fleet_session_refresh_loop(ttl)
                )
        except Exception as e:  # never block a fleet tool on the session-cred path
            logger.warning(f"[fleet-session] ensure failed: {e}")


    async def _fleet_session_refresh_loop(self, ttl: int) -> None:
        """Re-fetch the session fleet key before it expires (~85% of its TTL)."""
        import asyncio

        from .fleet_session import fetch_fleet_session_key

        while True:
            try:
                await asyncio.sleep(max(60, int(ttl * 0.85)))
                _key, new_ttl = await fetch_fleet_session_key()
                if new_ttl > 0:
                    ttl = new_ttl
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[fleet-session] refresh failed (will retry): {e}")
                await asyncio.sleep(60)


    @tool
    async def list_fleet_nodes(self) -> dict:
        """List the user's Fleet compute nodes for the web Cluster panel.

        In hub mode the panel calls the hub's ``/api/fleet/nodes``; in LOCAL mode
        there is no hub, so it invokes this over the chatroom connection instead.
        Reads the fleet registry via the FleetToolSet (creds resolved from
        ``FLEET_CONTROLLER_URL`` / ``FLEET_KEY`` in the environment) and returns
        the same flat node shape the hub serves, plus the controller url + join
        key so the add-node command is copy-paste ready. The node that IS this
        machine is marked ``is_self``.
        """
        import os

        await self._ensure_fleet_session_key()
        controller_url = os.environ.get("FLEET_CONTROLLER_URL", "")
        install_url = os.environ.get(
            "FLEET_INSTALL_URL",
            "https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest/install.sh",
        )
        if not (controller_url or os.environ.get("FLEET_NATS_URL")):
            return {
                "success": True,
                "count": 0,
                "nodes": [],
                "controller_url": "",
                "note": "No fleet configured on this backend.",
            }
        try:
            from pantheon.apps.builtin.fleet import FleetToolSet

            ts = getattr(self, "_fleet_ts", None)
            if ts is None:
                ts = FleetToolSet()
                await ts.run_setup()  # connect + start the refresh loop so its
                # short-lived creds stay fresh (else _read_nodes returns [] after
                # the credential expires and the panel shows 0 nodes)
                self._fleet_ts = ts
            raw = await ts._read_nodes()
            self_id = await ts._resolve_local_node(raw)
            nodes = []
            for n in raw:
                s = FleetToolSet._summarize(n)
                if self_id and s.get("node_id") == self_id:
                    s["is_self"] = True
                nodes.append(s)
            return {
                "success": True,
                "count": len(nodes),
                "nodes": nodes,
                "self_node_id": self_id,
                "controller_url": controller_url,
                "install_url": install_url,
                # The key PREFIX (not the secret) lets the panel identify the fleet
                # key in local mode without the platform-keys API. The FULL key still
                # never leaves the backend — it grants command execution on the nodes.
                "key_prefix": (
                    os.environ.get("FLEET_KEY") or os.environ.get("PANTHEON_API_KEY") or ""
                )[:12],
                "fleet_id": getattr(ts, "_fleet_id", "") or "",
            }
        except Exception as e:
            logger.error(f"list_fleet_nodes failed: {e}")
            return {"success": False, "count": 0, "nodes": [], "error": str(e)}


    @tool
    async def fleet_up_local(self) -> dict:
        """Auto-join THIS machine to the fleet as a node (local mode).

        When the user is logged in and fleet is configured, the web Cluster panel
        calls this so the local machine joins the fleet as a data-transfer node
        without the user running the install/join command by hand. Idempotent: if
        this machine is already registered, or a `fleet up` is already running, it
        does nothing. In a sandbox the entrypoint already does this, so it's a
        no-op there.
        """
        import os
        import shutil
        import subprocess

        await self._ensure_fleet_session_key()
        controller = os.environ.get("FLEET_CONTROLLER_URL", "")
        key = os.environ.get("FLEET_KEY") or os.environ.get("PANTHEON_API_KEY") or ""
        if not (controller and key):
            return {"success": False, "message": "Fleet not configured on this backend."}

        # Already registered as a node? Nothing to do.
        try:
            from pantheon.apps.builtin.fleet import FleetToolSet

            ts = getattr(self, "_fleet_ts", None)
            if ts is None:
                ts = FleetToolSet()
                await ts.run_setup()  # keep its creds fresh (shared _fleet_ts)
                self._fleet_ts = ts
            if await ts._resolve_local_node():
                return {"success": True, "already_joined": True,
                        "message": "This machine is already in the fleet."}
        except Exception:  # noqa: BLE001 — registry read is best-effort here
            pass

        # A `fleet up` already running for this machine? Don't spawn a second.
        try:
            r = subprocess.run(
                ["pgrep", "-f", "fleet up --controller"],
                capture_output=True, text=True, timeout=3,
            )
            if r.stdout.strip():
                return {"success": True, "already_running": True,
                        "message": "fleet up is already running."}
        except Exception:  # noqa: BLE001 — pgrep may be absent; fall through
            pass

        fleet_bin = shutil.which("fleet") or os.path.expanduser("~/.local/bin/fleet")
        if not (fleet_bin and os.path.exists(fleet_bin)):
            return {"success": False,
                    "message": "fleet binary not found — run the install command once."}

        # Detached so the node keeps serving tasks/transfers after this call.
        try:
            log = open("/tmp/pantheon-fleet-up.log", "ab")  # noqa: SIM115
            subprocess.Popen(
                [fleet_bin, "up", "--controller", controller, "--key", key],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"fleet_up_local failed to spawn: {e}")
            return {"success": False, "message": f"failed to start fleet up: {e}"}
        return {"success": True, "spawned": True,
                "message": "Joining this machine to the fleet…"}


    @tool
    async def fleet_down_local(self) -> dict:
        """Leave the fleet from THIS machine — stop the local `fleet up` node.

        Symmetric to fleet_up_local: the web Cluster panel / auth flow calls this
        when the user signs out (local mode) so the machine stops being a fleet
        node once fleet access is no longer authorized. SIGTERM lets `fleet up`
        deregister cleanly. Idempotent: a no-op if nothing is running.
        """
        import os
        import signal
        import subprocess

        try:
            r = subprocess.run(
                ["pgrep", "-f", "fleet up --controller"],
                capture_output=True, text=True, timeout=3,
            )
            pids = [p for p in r.stdout.split() if p.strip()]
        except Exception:  # noqa: BLE001 — pgrep absent → nothing we can stop
            pids = []
        if not pids:
            return {"success": True, "stopped": 0, "message": "No local fleet node was running."}
        stopped = 0
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGTERM)  # graceful leave (deregisters)
                stopped += 1
            except Exception:  # noqa: BLE001
                pass
        return {"success": True, "stopped": stopped,
                "message": f"Left the fleet ({stopped} node process stopped)."}


    @tool
    async def fleet_mint_join_token(self) -> dict:
        """Mint a single-use, short-lived join token to add ONE machine (local mode).

        The web Cluster panel calls this for the "add another machine" command so
        the displayed command carries a one-time token — safe to copy, screenshare,
        or log — instead of the reusable fleet key. The token works once and expires
        within minutes; a stolen token can add at most a single node before it dies.
        """
        import os

        import httpx

        await self._ensure_fleet_session_key()
        controller = os.environ.get("FLEET_CONTROLLER_URL", "")
        key = os.environ.get("FLEET_KEY") or os.environ.get("PANTHEON_API_KEY") or ""
        if not (controller and key):
            return {"success": False, "message": "Fleet not configured on this backend."}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{controller.rstrip('/')}/join-tokens", json={"key": key}
                )
            if r.status_code != 200:
                return {"success": False,
                        "message": f"controller {r.status_code}: {r.text[:200]}"}
            data = r.json()
            return {"success": True,
                    "join_token": data.get("join_token"),
                    "expires_at": data.get("expires_at"),
                    "controller": controller}
        except Exception as e:  # noqa: BLE001
            logger.error(f"fleet_mint_join_token failed: {e}")
            return {"success": False, "message": f"mint join token failed: {e}"}


    @tool
    async def fleet_revoke_node(self, node_id: str) -> dict:
        """Revoke a node from the fleet (local mode) — the Cluster panel Revoke button.

        Adds the node to the Controller's revocation list so it can no longer refresh
        its short-lived credential; the node drops off within the credential TTL.
        Authorized by the fleet key (which the fleet owner holds), so no admin/service
        token is needed. A revoked node must rejoin with a fresh identity.
        """
        import os

        import httpx

        await self._ensure_fleet_session_key()
        controller = os.environ.get("FLEET_CONTROLLER_URL", "")
        key = os.environ.get("FLEET_KEY") or os.environ.get("PANTHEON_API_KEY") or ""
        if not (controller and key):
            return {"success": False, "message": "Fleet not configured on this backend."}
        if not node_id:
            return {"success": False, "message": "node_id required."}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{controller.rstrip('/')}/revoke",
                    json={"key": key, "node_id": node_id},
                )
            if r.status_code != 200:
                return {"success": False,
                        "message": f"controller {r.status_code}: {r.text[:200]}"}
            data = r.json()
            return {"success": True, "node_id": node_id,
                    "node_pub": data.get("node_pub", ""),
                    "kicked": bool(data.get("kicked", False))}
        except Exception as e:  # noqa: BLE001
            logger.error(f"fleet_revoke_node failed: {e}")
            return {"success": False, "message": f"revoke failed: {e}"}
