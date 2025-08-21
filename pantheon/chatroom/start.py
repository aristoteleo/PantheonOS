import asyncio
from pathlib import Path

from pantheon.toolsets.endpoint import Endpoint

from .room import ChatRoom


async def start_services(
    service_name: str = "pantheon-chatroom",
    memory_dir: str = "./.pantheon-chatroom",
    id_hash: str | None = None,
    endpoint_service_id: str | None = None,
    workspace_path: str = "./.pantheon-chatroom-workspace",
    agents_template: dict | str | None = None,
    log_level: str = "INFO",
    endpoint_wait_time: int = 5,
    worker_params: dict | None = None,
    worker_params_endpoint: dict | None = None,
    endpoint_connect_params: dict | None = None,
    speech_to_text_model: str = "gpt-4o-mini-transcribe",
):
    """Start the chatroom service.

    Args:
        service_name: The name of the service.
        memory_dir: The directory to store the memory.
        id_hash: The hash of the ID, if you want a stable service ID please provide it.
        endpoint_service_id: The service ID of the remote endpoint.
        workspace_path: The path to the workspace.
        agents_template: The template of the agents.
        log_level: The level of the log.
        endpoint_wait_time: The time to wait for the endpoint to start.
        worker_params: The parameters for the worker.
        worker_params_endpoint: The parameters for the worker of the endpoint.
        endpoint_connect_params: The parameters for the endpoint connection.
        speech_to_text_model: The model to use for speech to text.
    """
    if endpoint_service_id is None:
        w_path = Path(workspace_path)
        w_path.mkdir(parents=True, exist_ok=True)
        endpoint = Endpoint(
            {
                "service_name": service_name,
                "workspace_path": workspace_path,
                "log_level": log_level,
                "allow_file_transfer": True,
                "builtin_services": [
                    {"type": "python_interpreter"},
                    "file_manager",
                    "web_browse",
                ],
                "outer_services": [],
                "docker_services": [],
            }
        )
        asyncio.create_task(endpoint.run())
        endpoint_service_id = endpoint.worker.service_id
        await asyncio.sleep(endpoint_wait_time)

    if worker_params is None:
        worker_params = {}
    if "id_hash" not in worker_params:
        worker_params["id_hash"] = id_hash

    chat_room = ChatRoom(
        endpoint_service_id=endpoint_service_id,
        agents_template=agents_template,
        memory_dir=memory_dir,
        name=service_name,
        worker_params=worker_params,
        remote_service_params=endpoint_connect_params,
        speech_to_text_model=speech_to_text_model,
    )
    await chat_room.run(log_level=log_level)
