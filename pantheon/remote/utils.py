import asyncio
from magique.client import connect_to_server, ServiceProxy

from .constant import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT


async def connect_remote(
        service_name_or_id: str,
        server_host: str = DEFAULT_SERVER_HOST,
        server_port: int = DEFAULT_SERVER_PORT,
        server_timeout: float = 5.0,
        service_timeout: float = 10.0,
        time_delta: float = 0.5,
        try_direct_connection: bool = True,
        ) -> ServiceProxy:
    server = None
    async def _get_server():
        nonlocal server
        while server is None:
            try:
                server = await connect_to_server(
                    server_host,
                    server_port,
                )
            except Exception:
                await asyncio.sleep(time_delta)

    await asyncio.wait_for(_get_server(), server_timeout)
    service = None

    async def _get_service():
        nonlocal service
        while service is None:
            try:
                service = await server.get_service(service_name_or_id, try_direct_connection=try_direct_connection)
            except ValueError:
                await asyncio.sleep(time_delta)

    await asyncio.wait_for(_get_service(), service_timeout)
    return service
