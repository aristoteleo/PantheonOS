import asyncio
import uuid
from typing import Any, Dict, Optional, Callable, List
from hypha_rpc import connect_to_server

from .base import RemoteBackend, RemoteService, RemoteWorker, ServiceInfo


class HyphaBackend(RemoteBackend):
    """Hypha RPC implementation of RemoteBackend"""

    def __init__(
        self,
        server_url: Optional[str] = None,
        workspace: Optional[str] = None,
        token: Optional[str] = None,
        **kwargs,
    ):
        self.server_url = server_url or "https://ai.imjoy.io"
        self.workspace = workspace
        self.token = token
        self.default_kwargs = kwargs
        self._server = None

    async def _get_server(self):
        """Get or create hypha server connection"""
        if not self._server:
            connect_kwargs = {"server_url": self.server_url, **self.default_kwargs}
            if self.workspace:
                connect_kwargs["workspace"] = self.workspace
            if self.token:
                connect_kwargs["token"] = self.token

            self._server = await connect_to_server(**connect_kwargs)
        return self._server

    async def connect(self, service_id: str, **kwargs) -> "HyphaService":
        """Connect to a remote service by service ID"""
        server = await self._get_server()

        # Get the service from the server
        try:
            service = await server.get_service(service_id)
            return HyphaService(service, service_id)
        except Exception as e:
            # If service_id is actually a service name pattern, search for it
            services = await server.list_services()
            matching_services = [s for s in services if service_id in s["id"]]
            if matching_services:
                service = await server.get_service(matching_services[0]["id"])
                return HyphaService(service, matching_services[0]["id"])
            raise Exception(f"Service {service_id} not found: {e}")

    def create_worker(self, service_name: str, **kwargs) -> "HyphaRemoteWorker":
        """Create a worker for serving functions (synchronous, connection delayed until run)"""
        return HyphaRemoteWorker(self, service_name, **kwargs)

    @property
    def servers(self) -> list[str]:
        """Get server URLs"""
        return [self.server_url]


class HyphaService(RemoteService):
    """Hypha implementation of RemoteService"""

    def __init__(self, service, service_id: str):
        self._service = service
        self._service_id = service_id
        self._service_name = getattr(service, "name", service_id)

    async def invoke(self, method: str, parameters: Dict[str, Any] = None) -> Any:
        """Invoke a remote method via Hypha RPC"""
        if not hasattr(self._service, method):
            raise AttributeError(f"Service does not have method '{method}'")

        method_func = getattr(self._service, method)

        if parameters:
            # Handle both keyword and positional arguments
            if isinstance(parameters, dict):
                return await method_func(**parameters)
            elif isinstance(parameters, list):
                return await method_func(*parameters)
            else:
                return await method_func(parameters)
        else:
            return await method_func()

    async def close(self):
        """Close connection - handled by server"""
        # Hypha server handles connection lifecycle
        pass

    @property
    def service_info(self) -> ServiceInfo:
        """Get service information"""
        return ServiceInfo(
            service_id=self._service_id,
            service_name=self._service_name,
            description=getattr(self._service, "description", ""),
            functions_description=getattr(self._service, "functions_description", {}),
        )

    async def fetch_service_info(self) -> ServiceInfo:
        """Fetch service information"""
        return self.service_info


class HyphaRemoteWorker(RemoteWorker):
    """Hypha implementation of RemoteWorker"""

    def __init__(
        self,
        backend: "HyphaBackend",
        service_name: str,
        service_id: Optional[str] = None,
        **kwargs,
    ):
        self._backend = backend
        self.server = None  # Will be set during run()
        self._service_name = service_name
        self._service_id = service_id or f"{service_name}_{str(uuid.uuid4())[:8]}"
        self._functions: Dict[str, Callable] = {}
        self._service = None
        self._running = False
        self.service_config = kwargs

    def register(self, func: Callable, name: Optional[str] = None, **kwargs):
        """Register a function for remote access"""
        func_name = name or func.__name__
        self._functions[func_name] = func

    async def run(self):
        """Start the Hypha worker by registering as a service"""
        if self._running:
            return

        # Get server connection
        self.server = await self._backend._get_server()
        self._running = True

        # Create service configuration
        service_config = {
            "id": self._service_id,
            "name": self._service_name,
            "type": "pantheon-agent",
            **self.service_config,
        }

        # Create service interface with all registered functions
        service_interface = {}

        for func_name, func in self._functions.items():
            if asyncio.iscoroutinefunction(func):
                service_interface[func_name] = func
            else:
                # Wrap sync functions to be async-compatible
                # Use closure to capture the current func
                def make_wrapper(f):
                    async def async_wrapper(*args, **kwargs):
                        return f(*args, **kwargs)

                    return async_wrapper

                service_interface[func_name] = make_wrapper(func)

        # Register the service with Hypha
        self._service = await self.server.register_service(
            service_interface, service_config
        )

        # Keep running (Hypha handles the service lifecycle)
        try:
            while self._running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            await self.stop()

    async def stop(self):
        """Stop the worker and unregister the service"""
        self._running = False
        if self._service:
            try:
                await self.server.unregister_service(self._service_id)
            except Exception:
                # Service might already be unregistered
                pass
            self._service = None

    @property
    def service_id(self) -> str:
        """Get the service ID"""
        return self._service_id

    @property
    def service_name(self) -> str:
        """Get the service name"""
        return self._service_name

    @property
    def servers(self) -> List[str]:
        """Get server URLs for backward compatibility"""
        if self.server and hasattr(self.server, "config"):
            return [self.server.config.get("server_url", "")]
        return []

    @property
    def functions(self) -> Dict[str, tuple]:
        """Get registered functions for compatibility with toolset interface"""
        return {
            name: (func, getattr(func, "__doc__", ""))
            for name, func in self._functions.items()
        }
