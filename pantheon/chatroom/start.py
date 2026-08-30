import asyncio
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode

from pantheon.utils.misc import generate_service_id
from pantheon.utils.log import logger

from .room import ChatRoom


def _is_wsl() -> bool:
    """Return True when running inside Windows Subsystem for Linux."""
    if platform.system().lower() != "linux":
        return False

    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return True

    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _open_url_in_windows_browser(url: str) -> bool:
    """
    Open a URL using the Windows default browser from WSL.

    Returns True once one of the Windows launch commands succeeds.

    Note: URLs are quoted to prevent shell metacharacters (e.g. & in query
    strings) from being interpreted by cmd.exe or PowerShell.
    """
    # Quote the URL so & and other shell metacharacters are not interpreted
    quoted = f'"{url}"'
    launch_commands = [
        ["powershell.exe", "-NoProfile", "-Command", f"Start-Process {quoted}"],
        ["cmd.exe", "/c", f"start \"\" {quoted}"],
    ]

    last_error = None
    for command in launch_commands:
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            last_error = exc
            logger.warning(f"[FRONTEND] WSL browser command failed: {command[0]}: {exc}")

    if last_error is not None:
        raise RuntimeError(
            f"Failed to open Windows browser from WSL using fallback commands: {last_error}"
        )

    return False


def _open_browser_url(url: str) -> bool:
    """Open a browser URL, preferring the Windows default browser under WSL."""
    import webbrowser

    if _is_wsl():
        try:
            return _open_url_in_windows_browser(url)
        except Exception as exc:
            logger.warning(
                f"[FRONTEND] WSL browser fallback failed, trying Linux opener instead: {exc}"
            )

    return bool(webbrowser.open(url))


async def start_services(
    service_name: str = None,
    memory_dir: str = None,
    workspace_path: str = None,
    log_level: str = None,
    speech_to_text_model: str = None,
    id_hash: str | None = None,
    nats_servers: str = None,
    auto_start_nats: bool = True,
    auto_ui: str | bool | None = True,
    sync_templates: bool = False,
):
    """Start the chatroom service.

    Args:
        service_name: The name of the service. (default from settings)
        memory_dir: The directory to store the memory. (default from settings)
        workspace_path: The path to the workspace. (default from settings)
        log_level: The level of the log. (default from settings)
        speech_to_text_model: The model to use for speech to text. (default from settings)
        id_hash: Hash string to generate stable service_id (e.g., "alice", "bob"). If not provided, generates a unique UUID per instance.
        nats_servers: NATS server URL(s). Supports WebSocket (wss://) and TCP (nats://).
                     Multiple servers separated by pipe (|). Overrides NATS_SERVERS env var.
                     Example: "wss://pantheon.aristoteleo.com/nats"
        auto_start_nats: Automatically start local NATS server.
                        Default: True (provides nats://localhost:4222 and ws://localhost:8080).
                        Disable with --no-auto-start-nats when connecting to an external NATS.
        auto_ui: Automatically open browser with auto-connect config when ready.
                Default: True (opens https://pantheon-ui.aristoteleo.com). Requires --auto-start-nats.
                Pass a custom URL like --auto-ui "http://localhost:5173" to point at a local dev
                frontend, or --no-auto-ui to suppress (e.g. headless / CI).
        sync_templates: Force-sync all factory templates (agents, teams, prompts, skills)
                       before starting. Useful after image upgrades.

    Note:
        API keys should be set via:
        - Environment variables: export OPENAI_API_KEY="sk-..."
        - .env file: OPENAI_API_KEY=sk-...
        - settings.json api_keys section

        Prefer provider-specific API keys and optional Base URLs.
        LLM_API_BASE acts as a global Base URL fallback when a provider-
        specific *_API_BASE is not configured. LLM_API_KEY remains an
        optional OpenAI-routed fallback key.
    """
    # DIAGNOSTIC: Log startup parameters for debugging
    logger.debug(f"[DIAGNOSTIC] start_services() called with auto_start_nats={auto_start_nats}, auto_ui={auto_ui}")

    if sync_templates:
        from pantheon.utils import log
        log.set_level(log_level or "INFO")
        from pantheon.factory.template_manager import get_template_manager
        logger.info("[sync-templates] Force-syncing factory templates...")
        tm = get_template_manager()
        total = tm.force_sync_factory_templates()
        logger.info(f"[sync-templates] Synced {total} template(s) from factory")

    # Hard kill-switch: an env var (set by the desktop and when the chatroom
    # spawns per-project endpoints) forces the browser auto-open OFF regardless
    # of the parsed flag — belt-and-suspenders against a misrouted subprocess.
    import os as _os
    if _os.getenv("PANTHEON_DISABLE_AUTO_UI", "").lower() in ("1", "true", "yes", "on"):
        auto_ui = False

    # Validate auto_ui parameter
    if auto_ui and not auto_start_nats:
        raise ValueError(
            "--auto-ui requires --auto-start-nats to be enabled.\n"
            "Usage: python -m pantheon.chatroom --auto-start-nats --auto-ui\n"
            "Or with custom URL: --auto-start-nats --auto-ui \"http://localhost:5173\""
        )

    # Helper function to open browser with auto-connect config
    def open_auto_connect_browser(
        frontend_url: str,
        nats_url: str,
        service_id: str,
    ) -> None:
        """
        Open browser with auto-connect configuration.

        Args:
            frontend_url: Frontend base URL (e.g., "https://pantheon-ui.vercel.app")
            nats_url: NATS WebSocket URL (e.g., "ws://localhost:8080")
            service_id: Service ID for connection
        """
        # Build full connection URL with parameters
        # For Vue Router hash mode, query parameters must come after the hash (#/)
        query = urlencode({"nats": nats_url, "service": service_id, "auto": "true"})
        connection_url = f"{frontend_url}/#/?{query}"

        logger.info("")
        logger.info("[FRONTEND] Opening browser for auto-connect...")
        logger.info(f"  Frontend URL: {frontend_url}")
        logger.info(f"  NATS WebSocket: {nats_url}")
        logger.info(f"  Service ID: {service_id}")
        logger.info(f"  Full Connection URL:")
        logger.info(f"  {connection_url}")
        logger.info("")

        try:
            # Try to open browser
            _open_browser_url(connection_url)
            logger.info("[FRONTEND] ✓ Browser opened successfully")
        except Exception as e:
            logger.warning(f"[FRONTEND] Could not open browser automatically: {e}")
            logger.warning(f"[FRONTEND] Please open manually: {connection_url}")

    # Helper function for zombie process cleanup
    async def cleanup_zombie_nats(work_dir: Path):
        """
        Clean up zombie NATS processes for this specific pantheon_dir.

        Only cleans up NATS instances tracked by this work_dir's instance file,
        avoiding interference with other chatroom instances.
        """
        logger.info("[STARTUP] Cleanup: Checking for zombie NATS processes...")

        import subprocess
        import signal
        import json

        pantheon_dir = work_dir / ".pantheon"
        instance_file = pantheon_dir / ".nats-instance.json"

        # Check if instance file exists
        if not instance_file.exists():
            logger.debug("[STARTUP] Cleanup: No instance file found, nothing to clean")
            return

        try:
            # Read instance file
            with open(instance_file, 'r') as f:
                instance_data = json.load(f)

            pid = instance_data.get("pid")
            if not pid:
                logger.debug("[STARTUP] Cleanup: Instance file has no PID")
                instance_file.unlink()
                return

            # Check if process is alive
            try:
                os.kill(pid, 0)  # Signal 0 checks if process exists
                logger.info(f"[STARTUP] Cleanup: Found zombie NATS process (PID={pid})")

                # Graceful terminate first
                try:
                    logger.info(f"[STARTUP] Cleanup: Terminating NATS (PID={pid})...")
                    os.kill(pid, signal.SIGTERM)
                    await asyncio.sleep(2)  # Wait for graceful shutdown

                    # Check if still alive
                    try:
                        os.kill(pid, 0)
                        # Still alive, force kill
                        logger.info(f"[STARTUP] Cleanup: Force killing NATS (PID={pid})...")
                        os.kill(pid, signal.SIGKILL)
                        await asyncio.sleep(1)
                    except (OSError, ProcessLookupError):
                        # Process terminated successfully
                        pass

                    logger.info("[STARTUP] Cleanup: NATS process terminated")

                except (OSError, ProcessLookupError):
                    logger.debug("[STARTUP] Cleanup: Process already terminated")

            except (OSError, ProcessLookupError):
                logger.debug(f"[STARTUP] Cleanup: Process PID={pid} not found (already dead)")

            # Remove instance file
            instance_file.unlink()
            logger.debug("[STARTUP] Cleanup: Removed instance file")

            # Extra wait time to ensure ports are released (TCP TIME_WAIT state)
            logger.info("[STARTUP] Cleanup: Waiting for ports to be released...")
            await asyncio.sleep(2)  # Give OS time to fully release ports

            logger.info("[STARTUP] Cleanup: Complete")

        except json.JSONDecodeError as e:
            logger.warning(f"[STARTUP] Cleanup: Invalid instance file: {e}")
            instance_file.unlink()
        except Exception as e:
            logger.debug(f"[STARTUP] Cleanup: Error during cleanup: {e}")

    # ========== STARTUP ==========
    logger.info("[STARTUP] Starting chatroom service...")
    logger.info(f"[STARTUP] Parameters: auto_start_nats={auto_start_nats}")
    logger.debug(f"[STARTUP] NATS_SERVERS env before: {os.environ.get('NATS_SERVERS', 'NOT SET')}")

    # Determine work directory once and create it
    work_dir_str = memory_dir or "./.pantheon/chatroom"
    work_dir = Path(work_dir_str).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # ========== Set log level early so NATS startup logs are visible ==========
    from pantheon.utils import log
    log.set_level(log_level or "INFO")

    # ========== NATS AUTO-START ==========
    nats_manager = None
    server_info = None
    if auto_start_nats:
        logger.info("[STARTUP] Auto-starting local NATS server...")

        # Validate: only supported in embedded mode
        if False:
            raise ValueError(
                "unreachable\n"
                "Please use embedded mode (default) or start NATS manually."
            )

        from .nats_manager import NATSManager

        # Find config template: first check package dir (pip install), then project root (dev)
        config_template = Path(__file__).parent / "nats-ws.conf"
        if not config_template.exists():
            config_template = Path(__file__).parent.parent.parent / "nats-ws.conf"

        if not config_template.exists():
            raise RuntimeError(
                "NATS config template not found.\n"
                "Searched:\n"
                f"  - {Path(__file__).parent / 'nats-ws.conf'}\n"
                f"  - {Path(__file__).parent.parent.parent / 'nats-ws.conf'}"
            )

        # Determine pantheon_dir for instance tracking
        # Note: We construct it from work_dir here because settings haven't been loaded yet
        pantheon_dir = work_dir / ".pantheon"

        # Clean up any zombie NATS processes from previous runs
        await cleanup_zombie_nats(work_dir)

        # Initialize NATS manager with pantheon_dir for instance isolation
        nats_manager = NATSManager(
            config_template_path=config_template,
            work_dir=work_dir,
            pantheon_dir=pantheon_dir,
        )

        try:
            # 1) Try project-managed NATS (started by us in a previous run)
            server_info = await nats_manager.detect_existing()

            # 2) Otherwise probe for an external NATS on standard ports
            if server_info is None:
                server_info = await nats_manager.detect_external()

            if server_info:
                origin = "external" if server_info.get("external") else "project-managed"
                logger.info(f"✓ Reusing existing NATS server ({origin})")
                logger.info(f"  TCP URL: {server_info['tcp_url']}")
                logger.info(f"  WebSocket URL: {server_info['ws_url']}")
                logger.info(f"  Monitoring: {server_info['http_url']}")
                # Not managed by us — don't stop it on exit
                nats_manager = None
            else:
                # No existing server, start a new one
                server_info = await nats_manager.start()

                logger.info(f"✓ NATS server started successfully")
                logger.info(f"  TCP URL: {server_info['tcp_url']}")
                logger.info(f"  WebSocket URL: {server_info['ws_url']}")
                logger.info(f"  Monitoring: {server_info['http_url']}")
                logger.info(f"  Logs: {server_info['log_file']}")
                logger.info(f"  PID: {server_info['pid']}")

            # Log frontend connection info prominently
            logger.info("")
            logger.info("[FRONTEND] WebSocket endpoint for local browser:")
            logger.info(f"  {server_info['ws_url']}")
            logger.info("[FRONTEND] To connect from external network:")
            from urllib.parse import urlparse as _urlparse
            _ws_port = _urlparse(server_info['ws_url']).port or 8080
            logger.info(f"  ws://<your-local-ip>:{_ws_port} (or use port forwarding/ngrok)")
            logger.info("")

            # Override nats_servers with local URL (this takes precedence over .env)
            nats_servers = server_info["tcp_url"]

            # Explicitly override environment variables to use local NATS
            old_nats_servers = os.environ.get("NATS_SERVERS")
            os.environ["NATS_SERVERS"] = nats_servers

            # Clear subject prefix for local auto-start mode (no hub isolation needed)
            # A stale NATS_SUBJECT_PREFIX from a previous hub session causes subject mismatch:
            # backend subscribes to "<prefix>.pantheon.service.<id>" but frontend pings "pantheon.service.<id>"
            old_prefix = os.environ.pop("NATS_SUBJECT_PREFIX", None)
            if old_prefix:
                logger.info(f"[STARTUP] Cleared stale NATS_SUBJECT_PREFIX: {old_prefix}")

            # Set WebSocket port for toolset.py logging (safe URL parsing)
            from urllib.parse import urlparse
            ws_url = server_info["ws_url"]
            parsed = urlparse(ws_url)
            ws_port = str(parsed.port) if parsed.port else '8080'
            os.environ["NATS_WS_PORT"] = ws_port

            if old_nats_servers and old_nats_servers != nats_servers:
                logger.info(f"[STARTUP] Overriding NATS server (from .env or external source)")
                logger.info(f"  Old: {old_nats_servers}")
                logger.info(f"  New: {nats_servers} (local auto-started)")
            else:
                logger.info(f"[STARTUP] Using local NATS server: {nats_servers}")

        except RuntimeError as e:
            logger.error(f"✗ Failed to start NATS server:")
            logger.error(f"  {e}")
            raise
        except ConnectionError as e:
            logger.error(f"✗ NATS server did not become ready:")
            logger.error(f"  {e}")
            # Cleanup on failure
            if nats_manager:
                await nats_manager.stop()
            raise

    # Override NATS_SERVERS if explicitly provided via command line (but NOT in auto-start mode)
    # In auto-start mode, we already set it above
    elif nats_servers:
        os.environ["NATS_SERVERS"] = nats_servers
        logger.info(f"[STARTUP] Using NATS servers (from CLI): {nats_servers}")

    from pantheon.settings import get_settings as get_settings_func

    # Load settings for defaults (CLI > Settings > code defaults)
    # Use mode='safe' to respect environment variables set above (e.g., from --auto-start-nats)
    # This ensures dynamically set variables (like local NATS address) take precedence over .env
    settings = get_settings_func(mode='safe')

    # IMPORTANT: After loading settings, verify and re-apply the NATS_SERVERS environment variable
    # This ensures the latest value takes precedence over any cached values in settings
    final_nats_servers = os.environ.get("NATS_SERVERS", "").strip()
    if final_nats_servers:
        logger.debug(f"[STARTUP] Final NATS_SERVERS in environment: {final_nats_servers}")

    # Apply defaults: CLI > Settings > code defaults
    service_name = service_name or settings.get(
        "endpoint.service_name", "pantheon-chatroom"
    )
    memory_dir = memory_dir or settings.get(
        "chatroom.memory_dir", str(settings.memory_dir)
    )
    workspace_path = workspace_path or settings.get(
        "endpoint.workspace_path", str(settings.work_dir)
    )
    log_level = log_level or settings.get("endpoint.log_level", "INFO")
    speech_to_text_model = speech_to_text_model or settings.get(
        "chatroom.speech_to_text_model", "gpt-4o-mini-transcribe"
    )

    # Convert all relative paths to absolute paths
    memory_dir = str(Path(memory_dir).resolve())
    workspace_path = str(Path(workspace_path).resolve())

    # ===== Ensure .env exists (create from template if missing) =====
    env_file = Path(workspace_path) / ".env"
    if not env_file.exists():
        env_template = Path(__file__).resolve().parent.parent / "factory" / "templates" / ".env.example"
        if env_template.exists():
            shutil.copy2(str(env_template), str(env_file))
            logger.info(f"[STARTUP] Created .env template at {env_file}")
        else:
            logger.warning(f"[STARTUP] .env.example template not found at {env_template}")

    # ===== Step 1: Identity =====
    if id_hash is None:
        # Unique per instance so concurrent chatrooms don't collide on a subject
        id_hash = str(uuid.uuid4())

    # ===== Step 2: Create ChatRoom =====
    # Toolsets are App instances placed by the fleet runner; there is no
    # endpoint to boot. The resolver reads its coordinates lazily from the
    # runner's runtime.json (PANTHEON_FLEET_STATE_DIR).
    chat_room = ChatRoom(
        memory_dir=memory_dir,
        workspace_path=workspace_path,
        name=service_name,
        speech_to_text_model=speech_to_text_model,
        enable_nats_streaming=True,  # Enable NATS streaming for remote service
        enable_auto_chat_name=True,  # Enable auto chat name for UI mode
        id_hash=id_hash,  # Pass id_hash to ensure stable Service ID
    )

    try:
        from pantheon.utils.model_selector import refresh_ollama_cache

        asyncio.create_task(refresh_ollama_cache(force=True))
    except Exception as exc:
        logger.debug("[STARTUP] Failed to prewarm Ollama detection: {}", exc)

    # ===== Step 2.5: Verify NATS TCP connectivity (diagnostic) =====
    if auto_start_nats and server_info is not None:
        nats_tcp_url = server_info["tcp_url"]
        logger.info(f"[STARTUP] Verifying NATS TCP connectivity: {nats_tcp_url}")
        logger.info(f"[STARTUP] NATS_SERVERS env: {os.environ.get('NATS_SERVERS', 'NOT SET')}")
        try:
            import nats as nats_lib
            test_nc = await asyncio.wait_for(
                nats_lib.connect(servers=[nats_tcp_url]),
                timeout=5
            )
            logger.info(f"[STARTUP] ✓ NATS TCP connection verified: {nats_tcp_url}")
            await test_nc.close()
        except Exception as e:
            logger.error(f"[STARTUP] ✗ NATS TCP connection FAILED: {nats_tcp_url} -> {e}")
            logger.error(f"[STARTUP]   Frontend WS may work but backend TCP does not!")

    # ===== Step 3: Start ChatRoom (always as remote service) =====
    # Launch as background task so we can wait for worker readiness before opening browser
    def _on_run_error(task: asyncio.Task):
        """Log errors from background run task immediately."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(f"[STARTUP] ChatRoom.run() failed: {exc}")

    run_task = asyncio.create_task(chat_room.run(log_level=log_level, remote=True))
    run_task.add_done_callback(_on_run_error)

    # ===== Step 3.5: Wait for worker to subscribe, then open browser / emit PANTHEON_READY =====
    if auto_start_nats and server_info is not None:
        # Wait for NATS worker to be ready (subscribed) before emitting ready event
        try:
            await asyncio.wait_for(chat_room._worker_ready.wait(), timeout=30)
            logger.info("[STARTUP] Worker is ready.")
        except asyncio.TimeoutError:
            # Check if run_task already failed
            if run_task.done() and run_task.exception():
                logger.error(f"[STARTUP] ChatRoom.run() failed before worker was ready: {run_task.exception()}")
            else:
                logger.warning("[STARTUP] Worker did not become ready within 30s, continuing anyway")

        # Calculate service ID based on id_hash
        service_id = generate_service_id(id_hash)

        # Get NATS WebSocket URL from server_info
        nats_ws_url = server_info.get("ws_url", "ws://127.0.0.1:8080")

        # ── Emit machine-parseable ready event ──────────────────────────────
        # Tauri (and any other host process) can listen on stdout for this line
        # to learn the WS URL and service_id without any inter-process RPC.
        import json as _json
        _ready_event = {
            "ws_url": nats_ws_url,
            "tcp_url": server_info.get("tcp_url", "nats://localhost:4222"),
            "service_id": service_id,
        }
        print(f"PANTHEON_READY:{_json.dumps(_ready_event)}", flush=True)
        logger.info(f"[STARTUP] PANTHEON_READY event emitted (service_id={service_id})")
        # ────────────────────────────────────────────────────────────────────

        # ── Auto-start configured Claw channels ─────────────────────────────
        try:
            from pantheon.claw import ClawConfigStore
            claw_cfg = ClawConfigStore().load()
            auto_channels = claw_cfg.get("auto_start") or []
            if auto_channels:
                gw_manager = chat_room._get_gateway_manager()
                for ch in auto_channels:
                    ch = str(ch).strip()
                    if ch:
                        res = gw_manager.start_channel(ch, source="auto_start")
                        logger.info(f"[STARTUP] Claw auto-start {ch}: {res}")
        except Exception as exc:
            logger.warning(f"[STARTUP] Claw auto-start failed: {exc}")
        # ────────────────────────────────────────────────────────────────────

        if auto_ui:
            # Determine frontend URL
            if isinstance(auto_ui, str):
                frontend_url = auto_ui
            else:
                # Default to production deployment
                frontend_url = "https://pantheon-ui.aristoteleo.com"

            # Open browser with auto-connect configuration
            open_auto_connect_browser(
                frontend_url=frontend_url,
                nats_url=nats_ws_url,
                service_id=service_id,
            )

    try:
        return await run_task
    finally:
        # ===== CLEANUP: Stop auto-started NATS =====
        if nats_manager is not None:
            logger.info("[CLEANUP] Stopping auto-started NATS server...")
            await nats_manager.stop()
