import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

import nats
from nats.js import JetStreamContext

from .base import RemoteBackend, RemoteService, RemoteWorker, ServiceInfo


@dataclass
class NATSMessage:
    method: str
    args: Dict[str, Any]
    reply_to: Optional[str] = None
    correlation_id: Optional[str] = None


class NATSBackend(RemoteBackend):
    """NATS implementation of RemoteBackend"""

    def __init__(self, server_urls: List[str] = None, **nats_kwargs):
        self.server_urls = server_urls or ["nats://localhost:4222"]
        self.nats_kwargs = nats_kwargs
        self._nc = None
        self._js = None

    async def _get_connection(self):
        if not self._nc:
            self._nc = await nats.connect(servers=self.server_urls, **self.nats_kwargs)
            self._js = self._nc.jetstream()
        return self._nc, self._js

    async def connect(self, service_id: str, **kwargs) -> "NATSService":
        nc, js = await self._get_connection()
        return NATSService(nc, js, service_id, **kwargs)

    async def create_worker(self, service_name: str, **kwargs) -> "NATSRemoteWorker":
        nc, js = await self._get_connection()
        return NATSRemoteWorker(nc, js, service_name, **kwargs)


class NATSService(RemoteService):
    def __init__(
        self, nc, js: JetStreamContext, service_id: str, timeout: float = 30.0
    ):
        self.nc = nc
        self.js = js
        self.service_id = service_id
        self.timeout = timeout
        self.subject_prefix = f"pantheon.agents.{service_id}"

    async def invoke(self, method: str, args: Dict[str, Any] = None) -> Any:
        """Invoke remote method via NATS request-reply"""
        subject = f"{self.subject_prefix}.{method}"

        message = NATSMessage(
            method=method, args=args or {}, correlation_id=str(uuid.uuid4())
        )
        payload = json.dumps(asdict(message)).encode()

        try:
            response = await self.nc.request(subject, payload, timeout=self.timeout)
            result = json.loads(response.data.decode())

            if result.get("error"):
                raise Exception(result["error"])
            return result.get("result")

        except asyncio.TimeoutError:
            raise Exception(f"Timeout calling {method} on {self.service_id}")

    async def close(self):
        # Connection managed by backend
        pass

    @property
    def service_info(self):
        """Get service information for NATS service"""
        # Create a simple service info object

        return ServiceInfo(service_id=self.service_id, service_name=self.service_id)


class NATSRemoteWorker(RemoteWorker):
    def __init__(self, nc, js: JetStreamContext, service_name: str, **kwargs):
        self.nc = nc
        self.js = js
        self._service_name = service_name
        self._service_id = (
            f"{service_name}_{str(uuid.uuid4())[:8]}"  # Generate unique ID
        )
        self.subject_prefix = f"pantheon.agents.{self._service_id}"
        self.functions: Dict[str, Callable] = {}
        self._running = False
        self._subscribers = []

    def register(self, func: Callable, name: Optional[str] = None):
        """Register function for remote access"""
        func_name = name or func.__name__
        self.functions[func_name] = func

    async def run(self):
        """Start the NATS worker"""
        self._running = True

        # Subscribe to all registered functions
        for func_name in self.functions.keys():
            subject = f"{self.subject_prefix}.{func_name}"
            sub = await self.nc.subscribe(subject, cb=self._handle_request)
            self._subscribers.append(sub)

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(0.1)

    async def stop(self):
        """Stop the worker"""
        self._running = False
        for sub in self._subscribers:
            await sub.unsubscribe()
        self._subscribers.clear()

    async def _handle_request(self, msg):
        """Handle incoming NATS requests"""
        try:
            data = json.loads(msg.data.decode())
            message = NATSMessage(**data)

            if message.method not in self.functions:
                error_response = {"error": f"Method {message.method} not found"}
                await msg.respond(json.dumps(error_response).encode())
                return

            func = self.functions[message.method]

            # Call function with arguments
            if asyncio.iscoroutinefunction(func):
                result = await func(**message.args)
            else:
                result = func(**message.args)

            # Send response
            response = {"result": result}
            await msg.respond(json.dumps(response).encode())

        except Exception as e:
            error_response = {"error": str(e)}
            await msg.respond(json.dumps(error_response).encode())

    @property
    def service_id(self) -> str:
        """Get the service ID"""
        return self._service_id

    @property
    def service_name(self) -> str:
        """Get the service name"""
        return self._service_name
