import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import cloudpickle
import nats
from funcdesc import parse_func

from ...utils.log import logger
from .base import RemoteBackend, RemoteService, RemoteWorker, ServiceInfo


@dataclass
class NATSMessage:
    method: str
    parameters: Dict[str, Any]
    reply_to: Optional[str] = None
    correlation_id: Optional[str] = None


class NATSBackend(RemoteBackend):
    """NATS implementation of RemoteBackend with 1:1 communication model (like Magique)"""

    def __init__(self, server_urls: list[str], **nats_kwargs):
        self.server_urls = server_urls or ["nats://localhost:4222"]
        self.nats_kwargs = nats_kwargs
        self._nc = None
        self._js = None
        self._kv = None

    async def _get_connection(self):
        if not self._nc:
            self._nc = await nats.connect(servers=self.server_urls, **self.nats_kwargs)
            # Initialize JetStream for KV store - REQUIRED
            try:
                self._js = self._nc.jetstream()
            except Exception as e:
                raise RuntimeError(f"JetStream is required but not available: {e}")

            # Create KV bucket for service discovery - REQUIRED
            try:
                self._kv = await self._js.key_value("pantheon-service")
            except Exception:
                try:
                    # Create the KV bucket if it doesn't exist
                    self._kv = await self._js.create_key_value(
                        bucket="pantheon-service"
                    )
                    logger.info("created NATS bucket: pantheon-service")
                except Exception as e:
                    raise RuntimeError(
                        f"KV store is required but could not be created: {e}"
                    )

            if not self._kv:
                raise RuntimeError("KV store is required but could not be initialized")
        return self._nc, self._js

    async def connect(self, service_id: str, **kwargs) -> "NATSService":
        nc, _ = await self._get_connection()
        service = NATSService(nc, service_id, kv_store=self._kv, **kwargs)
        # Fetch service info immediately after connection
        await service.fetch_service_info()
        return service

    def create_worker(self, service_name: str, **kwargs) -> "NATSRemoteWorker":
        return NATSRemoteWorker(self, service_name, **kwargs)

    @property
    def servers(self):
        return self.server_urls


class NATSService(RemoteService):
    """NATS service client - direct 1:1 communication with a specific service"""

    def __init__(
        self, nc, service_id: str, kv_store=None, timeout: float = 30.0, **kwargs
    ):
        self.nc = nc
        self.service_id = service_id
        self.kv_store = kv_store
        self.timeout = timeout
        # Direct subject for this specific service (like Magique's service addressing)
        self.service_subject = f"pantheon.service.{service_id}"
        self._service_info = ServiceInfo(
            service_id=service_id,
            service_name="",
            description="",
            functions_description={},
        )

    async def invoke(
        self, method: str, parameters: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Invoke remote method via NATS request-reply (1:1 like Magique)"""

        # Create request message
        message = NATSMessage(
            method=method, parameters=parameters or {}, correlation_id=str(uuid.uuid4())
        )
        payload = cloudpickle.dumps(message)

        try:
            # Direct request-reply to specific service (no work queue)
            response = await self.nc.request(
                self.service_subject, payload, timeout=self.timeout
            )
            result = cloudpickle.loads(response.data)
            logger.info(
                f"NATS client: requested function: {method} in service: {self.service_subject}"
            )
            if result.get("error"):
                raise Exception(result["error"])
            return result.get("result")

        except asyncio.TimeoutError:
            raise Exception(f"Timeout calling {method} on {self.service_id}")

    async def close(self):
        # Connection managed by backend
        pass

    async def fetch_service_info(self) -> ServiceInfo:
        """Fetch service information from KV store (no caching for dynamic functions)"""
        if self.kv_store:
            try:
                # Try to get service info from KV store
                entry = await self.kv_store.get(self.service_id)
                if entry:
                    # Use cloudpickle to deserialize and preserve original funcdesc objects
                    service_data = cloudpickle.loads(entry.value)
                    self._service_info = ServiceInfo(
                        service_id=service_data["service_id"],
                        service_name=service_data["service_name"],
                        description=service_data.get("description", ""),
                        functions_description=service_data.get(
                            "functions_description", {}
                        ),
                    )
            except Exception as e:
                raise RuntimeError(f"KV store get failed with error: {e}")
        return self._service_info

    @property
    def service_info(self) -> ServiceInfo:
        """Get service information (synchronous property for compatibility)"""
        return self._service_info


class NATSRemoteWorker(RemoteWorker):
    """NATS worker - listens for direct requests on its service subject"""

    def __init__(
        self, backend: "NATSBackend", service_name: str, description: str = "", **kwargs
    ):
        self._backend = backend
        self.nc = None  # Will be set during run()
        self.kv_store = None  # Will be set during run()
        self._service_name = service_name
        self._description = description

        # Generate service ID based on id_hash if provided (like Magique)
        id_hash = kwargs.get("id_hash")
        if id_hash:
            # Use SHA256 hash of id_hash to generate deterministic service ID
            hash_obj = hashlib.sha256(id_hash.encode())
            service_id_suffix = hash_obj.hexdigest()[:8]
            self._service_id = f"{service_name}_{service_id_suffix}"
        else:
            # Fallback to random UUID if no id_hash provided
            self._service_id = f"{service_name}_{str(uuid.uuid4())[:8]}"

        # Direct subject for this specific service instance
        self.service_subject = f"pantheon.service.{self._service_id}"
        self._functions: Dict[str, Callable] = {}
        self._running = False
        self._subscription = None

    def register(self, func: Callable, **kwargs):
        """Register function for remote access"""
        func_name = func.__name__
        self._functions[func_name] = func
        # Update KV store with new function info if running
        if self._running and self.kv_store:
            asyncio.create_task(self._register_to_kv_store())

    def unregister(self, name: str):
        """Unregister a function and update KV store"""
        if name in self._functions:
            del self._functions[name]
            # Update KV store with updated function info if running
            if self._running and self.kv_store:
                asyncio.create_task(self._register_to_kv_store())

    def get_service_info(self) -> ServiceInfo:
        """Create service info similar to Magique implementation"""
        functions_description = {}

        for name, func in self._functions.items():
            try:
                # Use parse_func like Magique does
                functions_description[name] = parse_func(func)
            except Exception:
                # Fallback for simple function description
                functions_description[name] = {
                    "name": name,
                    "description": getattr(func, "__doc__", ""),
                    "parameters": [],
                }

        return ServiceInfo(
            service_id=self._service_id,
            service_name=self._service_name,
            description=self._description,
            functions_description=functions_description,
        )

    async def _register_to_kv_store(self):
        """Register service information to KV store for discovery"""
        if not self.kv_store:
            return

        try:
            service_info = self.get_service_info()

            # Create service data for KV store - use cloudpickle to preserve original funcdesc objects
            service_data = {
                "service_id": service_info.service_id,
                "service_name": service_info.service_name,
                "description": service_info.description,
                "functions_description": service_info.functions_description,  # Keep original funcdesc objects
                "subject": self.service_subject,  # Include the NATS subject
                "registered_at": asyncio.get_event_loop().time(),
            }
            # FEATURE: switch back to json to ensure cross-language compatibility
            # Store in KV with service_id as key using cloudpickle
            await self.kv_store.put(self._service_id, cloudpickle.dumps(service_data))

            print(f"🔧 Service {self._service_id} registered to KV store")

        except Exception as e:
            print(f"⚠️ Failed to register to KV store: {e}")

    async def run(self):
        """Start the NATS worker - subscribe to direct requests"""
        # Initialize connection if not already done
        if self.nc is None:
            self.nc, _ = await self._backend._get_connection()
            self.kv_store = self._backend._kv

        self._running = True

        # Register service info to KV store for discovery
        await self._register_to_kv_store()

        # Subscribe to direct requests on our service subject (1:1 communication)
        self._subscription = await self.nc.subscribe(
            self.service_subject, cb=self._handle_request
        )

        logger.info(f"NATS worker: {self.service_subject} registered.")

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(0.1)

    async def stop(self):
        """Stop the worker and clean up subscription"""
        self._running = False

        # Clean up subscription
        if self._subscription:
            await self._subscription.unsubscribe()
            self._subscription = None

        # Remove from KV store
        if self.kv_store:
            try:
                await self.kv_store.delete(self._service_id)
                print(f"🔧 Service {self._service_id} unregistered from KV store")
            except Exception as e:
                print(f"⚠️ Failed to unregister from KV store: {e}")

    async def _handle_request(self, msg):
        """Handle incoming direct requests (like Magique's request handling)"""
        try:
            # Parse the message
            message: NATSMessage = cloudpickle.loads(msg.data)

            if message.method not in self._functions:
                error_response = {
                    "error": f"Method {message.method} not found on service {self._service_id}"
                }
                await msg.respond(cloudpickle.dumps(error_response))
                return
            logger.info(
                f"NATS worker:{self.service_subject} recevived function request: {message.method}"
            )
            func = self._functions[message.method]

            # Call function with arguments
            if asyncio.iscoroutinefunction(func):
                result = await func(**message.parameters)
            else:
                result = func(**message.parameters)
            logger.info(
                f"NATS worker:{self.service_subject} finished function request: {message.method}"
            )
            # Send response back to requester
            response = {"result": result}
            await msg.respond(cloudpickle.dumps(response))

        except Exception as e:
            error_response = {"error": str(e)}
            await msg.respond(cloudpickle.dumps(error_response))

    @property
    def service_id(self) -> str:
        """Get the unique service ID"""
        return self._service_id

    @property
    def service_name(self) -> str:
        """Get the service name"""
        return self._service_name

    @property
    def servers(self) -> List[str]:
        """Get server URLs for backward compatibility"""
        return self._backend.server_urls

    @property
    def functions(self) -> Dict[str, tuple]:
        """Get registered functions for compatibility with toolset interface"""
        # Return functions in the format expected by toolset (function, description)
        return {
            name: (func, getattr(func, "__doc__", ""))
            for name, func in self._functions.items()
        }
