import asyncio
import sys
import uuid
from pathlib import Path

from pantheon.endpoint import Endpoint, wait_endpoint_ready
from pantheon.remote import connect_remote
from pantheon.utils.misc import generate_service_id
from pantheon.utils.log import logger

from .room import ChatRoom


async def _start_endpoint_process(
    endpoint_id_hash: str,
    workspace_path: str,
    log_dir: Path,
) -> str:
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
) -> "Endpoint":
    """
    Start Endpoint in embedded mode (same event loop).

    Args:
        endpoint_id_hash: Hash to generate stable service_id
        workspace_path: Endpoint workspace directory

    Returns:
        Endpoint instance (not service_id)

    Raises:
        RuntimeError: If endpoint fails to start within timeout
    """
    logger.info(f"Starting Endpoint in embedded mode with id_hash={endpoint_id_hash}")

    endpoint = Endpoint(
        config=None, workspace_path=workspace_path, id_hash=endpoint_id_hash
    )
    # Start endpoint in background with remote=False (only setup, no worker)
    asyncio.create_task(endpoint.run(remote=False))

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
    service_name: str = "pantheon-chatroom",
    memory_dir: str = "./.pantheon-chatroom",
    endpoint_service_id: str | None = None,
    workspace_path: str = "./.pantheon-chatroom-workspace",
    agents_template: dict | str | None = None,
    log_level: str = "INFO",
    speech_to_text_model: str = "gpt-4o-mini-transcribe",
    endpoint_id_hash: str | None = None,
    endpoint_mode: str = "process",
    **kwargs,
):
    """Start the chatroom service.

    Args:
        service_name: The name of the service.
        memory_dir: The directory to store the memory.
        id_hash: The hash of the ID, if you want a stable service ID please provide it.
        endpoint_service_id: The service ID of the remote endpoint.
        workspace_path: The path to the workspace. Endpoint will chdir to this directory.
        agents_template: The template of the agents.
        log_level: The level of the log.
        speech_to_text_model: The model to use for speech to text.
        endpoint_id_hash: Fixed id_hash for endpoint to generate stable service_id. If not provided, auto-generated.
        endpoint_mode: How to start the endpoint. Options: "embedded" (same event loop),
                      "process" (independent subprocess).
    """
    # Convert all relative paths to absolute paths
    memory_dir = str(Path(memory_dir).resolve())
    workspace_path = str(Path(workspace_path).resolve())

    # Handle agents_template if it's a file path
    if agents_template and isinstance(agents_template, str):
        agents_template = str(Path(agents_template).resolve())

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
        if endpoint_id_hash is None:
            endpoint_id_hash = str(uuid.uuid4())

        # Start endpoint based on mode
        if endpoint_mode == "embedded":
            # Embed mode: return Endpoint instance
            endpoint = await _start_endpoint_embedded(
                endpoint_id_hash=endpoint_id_hash,
                workspace_path=workspace_path,
            )
        elif endpoint_mode == "process":
            # Process mode: return service_id
            log_dir = Path(memory_dir) / ".chatroom-logs"
            final_endpoint_service_id = await _start_endpoint_process(
                endpoint_id_hash=endpoint_id_hash,
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

    # ===== Step 2: Create ChatRoom =====
    chat_room = ChatRoom(
        endpoint=endpoint,  # Embed mode: has value; Process mode: None
        endpoint_service_id=final_endpoint_service_id,  # Process mode: has value; Embed mode: None
        agents_template=agents_template,
        memory_dir=memory_dir,
        name=service_name,
        speech_to_text_model=speech_to_text_model,
        **kwargs,
    )

    # ===== Step 3: Start ChatRoom (always as remote service) =====
    return await chat_room.run(log_level=log_level, remote=True)
