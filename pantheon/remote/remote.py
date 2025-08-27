import asyncio

from .backend import RemoteService
from .factory import connect_remote as _connect_remote
from ..utils.log import logger


async def connect_remote(
    service_name_or_id: str,
    server_urls: str | list[str] | None = None,
    backend: str | None = None,
    server_timeout: float = 10.0,
    service_timeout: float = 10.0,
    time_delta: float = 0.5,
) -> RemoteService:
    """
    Backward compatible connect_remote function that uses the new remote module.
    """

    async def _connect_with_retry():
        while True:
            try:
                service = await _connect_remote(
                    service_name_or_id, server_urls=server_urls, backend=backend
                )
                return service
            except Exception:
                await asyncio.sleep(time_delta)

    try:
        service = await asyncio.wait_for(
            _connect_with_retry(), server_timeout + service_timeout
        )
        logger.debug(f"Service {service_name_or_id} is available on servers")
        return service
    except asyncio.TimeoutError:
        error_msg = (
            f"Failed to get service {service_name_or_id} on servers: {server_urls}"
        )
        logger.debug(error_msg)
        raise Exception(error_msg)
