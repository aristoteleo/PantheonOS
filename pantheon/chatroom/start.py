import asyncio
from pathlib import Path

from pantheon.endpoint import Endpoint, wait_endpoint_ready

from .room import ChatRoom


async def start_services(
    service_name: str = "pantheon-chatroom",
    memory_dir: str = "./.pantheon-chatroom",
    endpoint_service_id: str | None = None,
    workspace_path: str = "./.pantheon-chatroom-workspace",
    agents_template: dict | str | None = None,
    log_level: str = "INFO",
    endpoint_wait_time: int = 5,
    speech_to_text_model: str = "gpt-4o-mini-transcribe",
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
        endpoint_wait_time: The time to wait for the endpoint to start.
        speech_to_text_model: The model to use for speech to text.
    """
    # Convert all relative paths to absolute paths
    # (Endpoint will chdir to its own workspace_path in __init__)
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

    if endpoint_service_id is None:
        endpoint = Endpoint(config=None, workspace_path=workspace_path)
        asyncio.create_task(endpoint.run())

        # Wait for endpoint to be ready
        while not await endpoint.services_ready():
            await asyncio.sleep(0.1)

        from pantheon.utils.log import logger

        logger.info(
            f"DEBUG: Endpoint ready! worker={endpoint.worker}, service_id={endpoint.service_id}, _setup_completed={endpoint._setup_completed}"
        )
        endpoint_service_id = endpoint.service_id
        logger.info(f"DEBUG: Got endpoint_service_id={endpoint_service_id}")

    chat_room = ChatRoom(
        endpoint_service_id=endpoint_service_id,
        agents_template=agents_template,
        memory_dir=memory_dir,
        name=service_name,
        speech_to_text_model=speech_to_text_model,
        **kwargs,
    )
    await chat_room.run(log_level=log_level)
