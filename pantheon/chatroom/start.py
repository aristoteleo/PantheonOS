import asyncio
import getpass
import os
import sys
import uuid
from pathlib import Path

# Note: pantheon.endpoint import is deferred to after NATS configuration
# This ensures environment variables are set before Endpoint reads them
from pantheon.utils.misc import generate_service_id
from pantheon.utils.log import logger

from .room import ChatRoom


async def _start_endpoint_process(
    endpoint_id_hash: str,
    workspace_path: str,
    log_dir: Path,
) -> str:
    from pantheon.remote import connect_remote

    """
    Start Endpoint in independent subprocess.

    Args:
        endpoint_id_hash: Hash to generate stable service_id
        workspace_path: Endpoint workspace directory
        log_dir: Directory to store endpoint logs

    Returns:
        endpoint_service_id for connecting to the endpoint

    Raises:
        RuntimeError: If endpoint fails to start within timeout
    """
    logger.info(
        f"Starting Endpoint in independent subprocess with id_hash={endpoint_id_hash}"
    )

    # Create log file for subprocess
    log_file = log_dir / "endpoint-subprocess.log"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build and print the command
    cmd = [
        sys.executable,
        "-m",
        "pantheon.endpoint",
        "start",
        "--workspace_path",
        workspace_path,
        "--id_hash",
        endpoint_id_hash,
    ]
    cmd_str = " ".join(cmd)
    logger.info(f"Executing command: {cmd_str}")

    with open(log_file, "w") as f:
        # Start Endpoint in independent subprocess to avoid resource contention
        endpoint_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=f,
            stderr=asyncio.subprocess.STDOUT,  # Combine stderr with stdout
        )

    logger.info(
        f"Endpoint subprocess started with PID={endpoint_proc.pid}, logs: {log_file}"
    )

    # Generate the endpoint service_id based on the fixed id_hash
    # Using generate_service_id() which matches NATSRemoteWorker logic
    endpoint_service_id = generate_service_id(endpoint_id_hash)

    # Wait for endpoint to be ready by polling the connection
    logger.info(f"Waiting for Endpoint to be ready (service_id={endpoint_service_id})")
    max_retries = 60  # Max 60 seconds
    retry_count = 0
    last_error = None

    while retry_count < max_retries:
        # Check if subprocess is still running
        # asyncio.subprocess.Process uses returncode instead of poll()
        if endpoint_proc.returncode is not None:
            # Process has exited with error, read logs for diagnosis
            logger.error(
                f"✗ Endpoint subprocess exited with code {endpoint_proc.returncode}"
            )
            try:
                with open(log_file, "r") as f:
                    logs = f.read()
                    if logs:
                        logger.error(f"Endpoint subprocess logs:\n{logs}")
            except Exception as read_err:
                logger.error(f"Could not read endpoint logs: {read_err}")
            raise RuntimeError(
                f"Endpoint subprocess exited with code {endpoint_proc.returncode}. "
                f"Check logs at: {log_file}"
            )

        try:
            # Try to connect to the endpoint
            # connect_remote() will raise ConnectionError if unable to connect
            remote = await asyncio.wait_for(
                connect_remote(endpoint_service_id), timeout=1.0
            )
            logger.info(
                f"✓ Endpoint is ready! Connected to service_id={endpoint_service_id}"
            )
            return endpoint_service_id
        except (ConnectionError, asyncio.TimeoutError) as e:
            # Connection not ready yet, continue retrying
            last_error = e
            retry_count += 1
            if retry_count % 10 == 0:
                logger.debug(
                    f"Endpoint not ready (attempt {retry_count}/{max_retries}): {type(e).__name__}"
                )
            await asyncio.sleep(1)
        except Exception as e:
            # Unexpected error, fail immediately
            logger.error(f"✗ Unexpected error connecting to endpoint: {e}")
            endpoint_proc.terminate()
            raise

    # Timeout: subprocess still running but failed to connect within max_retries
    logger.error(f"✗ Failed to connect to Endpoint within {max_retries} seconds")
    logger.error(f"  Last error: {type(last_error).__name__}")
    logger.error(f"  Endpoint logs: {log_file}")

    endpoint_proc.terminate()
    try:
        await asyncio.sleep(2)
        if endpoint_proc.returncode is None:
            # Still running after terminate, force kill
            endpoint_proc.kill()
    except Exception:
        pass

    raise ConnectionError(
        f"Unable to connect to Endpoint service_id '{endpoint_service_id}' "
        f"within {max_retries} seconds. "
        f"Check logs at: {log_file}"
    )


async def _start_endpoint_embedded(
    endpoint_id_hash: str,
    workspace_path: str,
    log_level: str = "INFO",
    enable_notebook_streaming: bool = False,
) -> "Endpoint":
    """
    Start Endpoint in embedded mode (same event loop).

    Args:
        endpoint_id_hash: Hash to generate stable service_id
        workspace_path: Endpoint workspace directory
        log_level: Log level for endpoint
        enable_notebook_streaming: Enable NATS streaming for notebook (default: False)

    Returns:
        Endpoint instance (not service_id)

    Raises:
        RuntimeError: If endpoint fails to start within timeout
    """
    # Deferred import: ensure NATS environment variables are set before importing Endpoint
    from pantheon.endpoint import Endpoint

    logger.info(f"Starting Endpoint in embedded mode with id_hash={endpoint_id_hash}")

    # Only set config if streaming is explicitly enabled
    config = {"enable_notebook_streaming": True} if enable_notebook_streaming else None

    endpoint = Endpoint(
        config=config,
        workspace_path=workspace_path,
        id_hash=endpoint_id_hash
    )
    # Start endpoint in background with remote=False (only setup, no worker)
    asyncio.create_task(endpoint.run(remote=False, log_level=log_level))


    # Wait for endpoint to be ready
    logger.info("Waiting for Endpoint to be ready (embedded mode)")
    max_retries = 30  # Max 3 seconds (30 * 0.1 second)
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Check if endpoint is ready
            if endpoint._setup_completed:
                logger.info(
                    f"✓ Endpoint initialized (embedded mode, service_id={endpoint.service_id})"
                )
                return endpoint
        except Exception as e:
            logger.debug(f"Endpoint not ready yet: {e}")

        retry_count += 1
        if retry_count % 5 == 0:
            logger.debug(
                f"Endpoint not ready yet (attempt {retry_count}/{max_retries})"
            )
        await asyncio.sleep(0.1)

    # Timeout
    logger.error(f"Failed to start Endpoint after {max_retries * 0.1} seconds")
    raise RuntimeError(f"Endpoint failed to start within {max_retries * 0.1} seconds")


async def start_services(
    service_name: str = None,
    memory_dir: str = None,
    endpoint_service_id: str | None = None,
    workspace_path: str = None,
    log_level: str = None,
    speech_to_text_model: str = None,
    id_hash: str | None = None,
    endpoint_mode: str = "embedded",
    nats_servers: str = None,
    auto_start_nats: bool = False,
    auto_ui: str | bool | None = None,
    **kwargs,
):
    # DIAGNOSTIC: Log startup parameters for debugging
    logger.debug(f"[DIAGNOSTIC] start_services() called with auto_start_nats={auto_start_nats}, auto_ui={auto_ui}")

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
            nats_url: NATS WebSocket URL (e.g., "ws://127.0.0.1:8080")
            service_id: Service ID for connection
        """
        import webbrowser

        # Build full connection URL with parameters
        # For Vue Router hash mode, query parameters must come after the hash (#/)
        connection_url = (
            f"{frontend_url}/#/?nats={nats_url}&service={service_id}&auto=true"
        )

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
            webbrowser.open(connection_url)
            logger.info("[FRONTEND] ✓ Browser opened successfully")
        except Exception as e:
            logger.warning(f"[FRONTEND] Could not open browser automatically: {e}")
            logger.warning(f"[FRONTEND] Please open manually: {connection_url}")

    # Helper function for zombie process cleanup
    async def cleanup_zombie_nats(work_dir: Path):
        """Clean up zombie NATS processes and stale config from previous runs."""
        logger.info("[STARTUP] Cleanup: Checking for zombie NATS processes...")

        import subprocess
        import signal

        nats_cleaned = False

        # Process cleanup - try pgrep first, then fallback to pkill
        process_cleanup_ok = False
        try:
            result = subprocess.run(
                ["pgrep", "-f", "nats-server"],
                capture_output=True,
                text=True,
                timeout=2
            )

            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                # Changed from WARNING to INFO - finding processes is normal, not an error
                logger.info(f"[STARTUP] Cleanup: Found {len(pids)} existing NATS process(es)")

                # Graceful terminate first
                for pid_str in pids:
                    try:
                        pid = int(pid_str)
                        logger.info(f"[STARTUP] Cleanup: Terminating NATS (PID={pid})...")
                        os.kill(pid, signal.SIGTERM)
                    except (ValueError, ProcessLookupError):
                        pass

                await asyncio.sleep(1)  # Wait for graceful shutdown

                # Force kill remaining
                subprocess.run(
                    ["pkill", "-9", "-f", "nats-server"],
                    capture_output=True,
                    timeout=2
                )

                logger.info("[STARTUP] Cleanup: NATS processes terminated")
                process_cleanup_ok = True
                nats_cleaned = True

        except FileNotFoundError:
            # pgrep not available, try pkill fallback
            logger.debug("[STARTUP] Cleanup: pgrep not available, using pkill fallback...")
            try:
                subprocess.run(
                    ["pkill", "-f", "nats-server"],
                    capture_output=True,
                    timeout=2
                )
                await asyncio.sleep(1)

                subprocess.run(
                    ["pkill", "-9", "-f", "nats-server"],
                    capture_output=True,
                    timeout=2
                )
                process_cleanup_ok = True
                nats_cleaned = True
            except subprocess.TimeoutExpired:
                logger.warning("[STARTUP] Cleanup: pkill timed out")
            except Exception as e:
                logger.debug(f"[STARTUP] Cleanup: Could not use pkill: {e}")

        except subprocess.TimeoutExpired:
            logger.warning("[STARTUP] Cleanup: pgrep timed out")
        except Exception as e:
            logger.debug(f"[STARTUP] Cleanup: Error checking processes: {e}")

        # File cleanup - separate try-except block
        try:
            nats_dir = work_dir / ".pantheon/chatroom"
            if nats_dir.exists():
                stale_files = list(nats_dir.glob(".nats-*"))
                if stale_files:
                    logger.info(f"[STARTUP] Cleanup: Removing {len(stale_files)} stale NATS file(s)...")
                    for file in stale_files:
                        try:
                            file.unlink()
                            logger.debug(f"[STARTUP] Cleanup: Removed {file.name}")
                        except Exception as e:
                            logger.warning(f"[STARTUP] Cleanup: Could not remove {file.name}: {e}")
        except Exception as e:
            logger.debug(f"[STARTUP] Cleanup: Error removing stale files: {e}")

        # Extra wait time to ensure ports are released (TCP TIME_WAIT state)
        if nats_cleaned:
            logger.info("[STARTUP] Cleanup: Waiting for ports to be released...")
            await asyncio.sleep(2)  # Give OS time to fully release ports

        # Final status message
        if nats_cleaned:
            logger.info("[STARTUP] Cleanup: Complete")
        else:
            logger.debug("[STARTUP] Cleanup: No zombie NATS processes found")

    """Start the chatroom service.

    Args:
        service_name: The name of the service. (default from settings)
        memory_dir: The directory to store the memory. (default from settings)
        endpoint_service_id: The service ID of the remote endpoint.
        workspace_path: The path to the workspace. (default from settings)
        log_level: The level of the log. (default from settings)
        speech_to_text_model: The model to use for speech to text. (default from settings)
        id_hash: Hash string to generate stable service_id (e.g., "alice", "bob"). If not provided, uses current username for reproducible ID.
        endpoint_mode: How to start the endpoint. Options: "embedded" (same event loop),
                      "process" (independent subprocess).
        nats_servers: NATS server URL(s). Supports WebSocket (wss://) and TCP (nats://).
                     Multiple servers separated by pipe (|). Overrides NATS_SERVERS env var.
                     Example: "wss://pantheon.aristoteleo.com/nats"
        auto_start_nats: Automatically start local NATS server (only works with --endpoint-mode embedded).
                        Default: False. When enabled, provides nats://localhost:4222 and ws://127.0.0.1:8080.
        auto_ui: Automatically open browser with auto-connect config when endpoint is ready.
                Default: False. Requires --auto-start-nats. Can specify custom URL or use default
                Vercel deployment. Examples: --auto-ui or --auto-ui "http://localhost:5173"

    Note:
        API keys should be set via:
        - Environment variables: export OPENAI_API_KEY="sk-..."
        - .env file: OPENAI_API_KEY=sk-...
        - settings.json api_keys section

        Use LiteLLM Proxy mode for secure API key handling (LITELLM_PROXY_ENABLED environment variable).
    """
    # ========== STARTUP ==========
    logger.info("[STARTUP] Starting chatroom service...")
    logger.info(f"[STARTUP] Parameters: auto_start_nats={auto_start_nats}, endpoint_mode={endpoint_mode}")
    logger.debug(f"[STARTUP] NATS_SERVERS env before: {os.environ.get('NATS_SERVERS', 'NOT SET')}")

    # Determine work directory once and create it
    work_dir_str = memory_dir or "./.pantheon/chatroom"
    work_dir = Path(work_dir_str).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # ========== NATS AUTO-START ==========
    nats_manager = None
    server_info = None
    if auto_start_nats:
        logger.info("[STARTUP] Auto-starting local NATS server...")

        # Clean up zombie processes and stale configs from previous runs
        await cleanup_zombie_nats(work_dir)

        # Validate: only supported in embedded mode
        if endpoint_mode != "embedded":
            raise ValueError(
                "Auto-start NATS is only supported with --endpoint-mode embedded.\n"
                f"Current mode: {endpoint_mode}\n\n"
                "Please use embedded mode (default) or start NATS manually."
            )

        from .nats_manager import NATSManager

        # Find config template (nats-ws.conf in project root)
        # Path structure: __file__ -> start.py (chatroom/) -> pantheon/ -> pantheon-agents/ (project root)
        package_dir = Path(__file__).parent.parent.parent
        config_template = package_dir / "nats-ws.conf"

        if not config_template.exists():
            raise RuntimeError(
                f"NATS config template not found: {config_template}\n"
                f"Expected at project root: nats-ws.conf\n"
                f"Current search path: {config_template}"
            )

        # Initialize NATS manager
        nats_manager = NATSManager(
            config_template_path=config_template,
            work_dir=work_dir,
        )

        try:
            # Start NATS server
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
            logger.info(f"  ws://<your-local-ip>:8080 (or use port forwarding/ngrok)")
            logger.info("")

            # Override nats_servers with local URL (this takes precedence over .env)
            nats_servers = server_info["tcp_url"]

            # Explicitly override environment variables to use local NATS
            old_nats_servers = os.environ.get("NATS_SERVERS")
            os.environ["NATS_SERVERS"] = nats_servers

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

    # Convert any other Path-like kwargs to absolute paths
    for key in list(kwargs.keys()):
        if key.endswith("_path") or key.endswith("_dir"):
            if isinstance(kwargs[key], str):
                kwargs[key] = str(Path(kwargs[key]).resolve())

    # ===== Step 1: Start or connect Endpoint =====
    endpoint = None
    final_endpoint_service_id = endpoint_service_id

    if final_endpoint_service_id is None:
        # Generate id_hash if not provided
        if id_hash is None:
            # Use current username for stable, reproducible Service ID
            id_hash = getpass.getuser()

        # Start endpoint based on mode
        if endpoint_mode == "embedded":
            # Embed mode: return Endpoint instance
            endpoint = await _start_endpoint_embedded(
                endpoint_id_hash=id_hash,
                workspace_path=workspace_path,
                log_level=log_level,
                enable_notebook_streaming=True,  # Enable streaming for chatroom
            )
        elif endpoint_mode == "process":
            # Process mode: return service_id
            log_dir = Path(memory_dir) / ".chatroom-logs"
            final_endpoint_service_id = await _start_endpoint_process(
                endpoint_id_hash=id_hash,
                workspace_path=workspace_path,
                log_dir=log_dir,
            )
        else:
            raise ValueError(
                f"Invalid endpoint_mode: {endpoint_mode}. "
                f"Must be 'process' or 'embedded'"
            )
    else:
        # Using existing endpoint_service_id
        endpoint_mode = "process"

    # ===== Step 1.5: Auto-open browser if requested =====
    if auto_ui and auto_start_nats and server_info is not None:
        # Determine frontend URL
        if isinstance(auto_ui, str):
            frontend_url = auto_ui
        else:
            # Default to Vercel deployment
            frontend_url = "https://pantheon-ui.vercel.app"

        # Calculate service ID based on id_hash
        service_id = generate_service_id(id_hash)

        # Get NATS WebSocket URL from server_info
        nats_ws_url = server_info.get("ws_url", "ws://127.0.0.1:8080")

        # Open browser with auto-connect configuration
        open_auto_connect_browser(
            frontend_url=frontend_url,
            nats_url=nats_ws_url,
            service_id=service_id,
        )

    # ===== Step 2: Create ChatRoom =====
    chat_room = ChatRoom(
        endpoint=endpoint if endpoint is not None else final_endpoint_service_id,
        memory_dir=memory_dir,
        name=service_name,
        speech_to_text_model=speech_to_text_model,
        enable_nats_streaming=True,  # Enable NATS streaming for remote service
        enable_auto_chat_name=True,  # Enable auto chat name for UI mode
        learning_config=settings.get_learning_config(),
        id_hash=id_hash,  # Pass id_hash to ensure stable Service ID
        **kwargs,
    )

    # ===== Step 3: Start ChatRoom (always as remote service) =====
    try:
        return await chat_room.run(log_level=log_level, remote=True)
    finally:
        # ===== CLEANUP: Stop auto-started NATS =====
        if nats_manager is not None:
            logger.info("[CLEANUP] Stopping auto-started NATS server...")
            await nats_manager.stop()
