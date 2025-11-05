"""
NATS Remote Backend Implementation
Integrates RPC calls and streaming functionality using Core NATS + JetStream KV
"""

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import cloudpickle
import nats
from nats.js.errors import NotFoundError, KeyNotFoundError
from funcdesc import parse_func, Description

from ...utils.log import logger
from ...utils.misc import run_func
from .base import (
    RemoteBackend,
    RemoteService,
    RemoteWorker,
    ServiceInfo,
    StreamType,
    StreamMessage,
    StreamChannel,
)


@dataclass
class NATSMessage:
    method: str
    parameters: Dict[str, Any]
    reply_to: Optional[str] = None
    correlation_id: Optional[str] = None


class NATSStreamChannel(StreamChannel):
    """NATS Core stream channel implementation using native pub/sub for high performance and low latency"""

    def __init__(self, stream_id: str, stream_type: StreamType, backend: "NATSBackend"):
        self._stream_id = stream_id
        self._stream_type = stream_type
        self._backend = backend
        self._closed = False
        self._subject = f"pantheon.stream.{stream_id}"

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def stream_type(self) -> StreamType:
        return self._stream_type

    async def publish(self, message: StreamMessage) -> None:
        if self._closed:
            raise RuntimeError(f"Stream channel {self._stream_id} is closed")

        nc, _ = await self._backend._get_connection()
        message.session_id = self._stream_id
        if not message.timestamp:
            message.timestamp = time.time()

        # Use JSON for stream messages to be compatible with frontend
        payload = json.dumps(message.to_dict()).encode("utf-8")
        await nc.publish(self._subject, payload)

    async def subscribe(self, callback: Callable[[StreamMessage], None]) -> str:
        """订阅NATS流消息"""
        if self._closed:
            raise RuntimeError("StreamChannel is closed")

        nc, _ = await self._backend._get_connection()

        async def message_handler(msg):
            try:
                # 解析JSON消息
                payload = json.loads(msg.data.decode("utf-8"))
                # 转换为StreamMessage对象
                from .base import StreamMessage

                stream_message = StreamMessage.from_dict(payload)
                # 调用回调函数 - 使用run_func自动处理同步/异步
                await run_func(callback, stream_message)
            except Exception as e:
                logger.error(f"Error processing NATS stream message: {e}")

        # 订阅NATS subject
        subscription = await nc.subscribe(self._subject, cb=message_handler)
        subscription_id = str(id(subscription))

        # 存储subscription以便后续取消订阅
        if not hasattr(self, "_subscriptions"):
            self._subscriptions = {}
        self._subscriptions[subscription_id] = subscription

        logger.debug(f"NATS stream subscribed: {self._subject} -> {subscription_id}")
        return subscription_id

    async def unsubscribe(self, subscription_id: str) -> bool:
        """取消订阅NATS流消息"""
        if (
            not hasattr(self, "_subscriptions")
            or subscription_id not in self._subscriptions
        ):
            logger.warning(f"Subscription not found: {subscription_id}")
            return False

        try:
            subscription = self._subscriptions[subscription_id]
            await subscription.unsubscribe()
            del self._subscriptions[subscription_id]
            logger.debug(f"NATS stream unsubscribed: {subscription_id}")
            return True
        except Exception as e:
            logger.error(f"Error unsubscribing NATS stream: {e}")
            return False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True


class NATSBackend(RemoteBackend):
    """NATS remote backend - Core NATS streaming + JetStream KV storage"""

    def __init__(self, server_urls: list[str], **nats_kwargs):
        self.server_urls = server_urls or ["nats://localhost:4222"]
        self.nats_kwargs = nats_kwargs
        self._nc: nats.aio.client.Client = None
        self._js = None  # Only used for KV store
        self._kv = None  # JetStream KV store

        # Core NATS stream management
        self._streams: Dict[str, NATSStreamChannel] = {}

    async def _get_connection(self):
        """Get NATS connection, JetStream only for KV storage"""
        if not self._nc:
            self._nc = await nats.connect(servers=self.server_urls, **self.nats_kwargs)

            # Initialize JetStream only for KV store
            try:
                self._js = self._nc.jetstream()

                # Create KV bucket for service discovery
                try:
                    self._kv = await self._js.key_value("pantheon-service")
                except Exception:
                    try:
                        self._kv = await self._js.create_key_value(
                            bucket="pantheon-service"
                        )
                        logger.debug("Created NATS KV bucket: pantheon-service")
                    except Exception as e:
                        logger.warning(
                            f"KV store creation failed: {e}, continuing without KV store"
                        )
                        self._kv = None

            except Exception as e:
                logger.warning(
                    f"JetStream not available: {e}, continuing without KV store"
                )
                self._js = None
                self._kv = None

        return self._nc, self._js

    # RPC interface implementation
    async def connect(self, service_id: str, **kwargs) -> "NATSService":
        """Connect to remote service

        Args:
            service_id: The service ID to connect to

        Returns:
            NATSService instance

        Raises:
            ValueError: If service_id is invalid
            ConnectionError: If unable to connect to service or fetch service info
        """
        if not service_id:
            raise ValueError("service_id cannot be None")

        nc, _ = await self._get_connection()
        service = NATSService(nc, service_id, kv_store=self._kv, **kwargs)

        try:
            await service.fetch_service_info()
        except Exception as e:
            # Convert any fetch_service_info error to ConnectionError
            # This abstracts away KV Store implementation details
            # External code only cares: "can I connect to this service_id?"
            raise ConnectionError(
                f"Failed to connect to service '{service_id}': {str(e)}"
            ) from e

        return service

    def create_worker(self, service_name: str, **kwargs) -> "NATSRemoteWorker":
        """Create remote worker"""
        return NATSRemoteWorker(self, service_name, **kwargs)

    @property
    def servers(self):
        return self.server_urls

    # Core NATS streaming interface implementation
    async def get_or_create_stream(
        self, stream_id: str, stream_type: StreamType = StreamType.CUSTOM, **kwargs
    ) -> StreamChannel:
        """Get existing stream or create new Core NATS streaming channel"""
        await self._get_connection()  # Ensure connection is established

        # Check if already exists
        existing_stream = self._streams.get(stream_id)
        if existing_stream:
            logger.debug(
                f"Core NATS stream {stream_id} already exists, returning existing"
            )
            return existing_stream

        # Create new Core NATS stream
        stream_channel = NATSStreamChannel(stream_id, stream_type, self)
        self._streams[stream_id] = stream_channel

        logger.debug(
            f"Created Core NATS stream: {stream_id} (type: {stream_type.value})"
        )
        return stream_channel


class NATSService(RemoteService):
    """NATS service client"""

    def __init__(
        self,
        nc: nats.aio.client.Client,
        service_id: str,
        kv_store=None,
        timeout: float = 30.0,
        **kwargs,
    ):
        self.nc = nc
        self.service_id = service_id
        self.kv_store = kv_store
        self.timeout = timeout
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
        """Invoke remote method with cloudpickle priority for backend-to-backend communication

        Supports reverse callable: if parameters contain callable objects, they will be
        wrapped as ReverseCallable so the worker can call them back.
        """
        if parameters is None:
            parameters = {}

        # Use ReverseCallContext to handle reverse call setup/teardown
        async with ReverseCallContext(self.nc, parameters) as processed_parameters:
            message = NATSMessage(
                method=method,
                parameters=processed_parameters,
                correlation_id=str(uuid.uuid4()),
            )

            # Use cloudpickle first for backend-to-backend communication
            payload = cloudpickle.dumps(message)

            try:
                response = await self.nc.request(
                    self.service_subject, payload, timeout=self.timeout
                )
            except asyncio.TimeoutError:
                raise Exception(f"Timeout calling {method} on {self.service_id}")

            # Try to parse response as cloudpickle first
            try:
                result = cloudpickle.loads(response.data)
            except Exception:
                # Fallback to JSON for frontend services
                try:
                    result = json.loads(response.data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise Exception(
                        f"Unable to decode response from service {self.service_id}"
                    )

            logger.debug(
                f"NATS client: requested function: {method} in service: {self.service_subject}"
            )

            if result.get("error"):
                raise Exception(result["error"])
            return result.get("result")

    async def close(self):
        pass

    async def fetch_service_info(self) -> ServiceInfo:
        """Fetch service information from KV store using JSON"""
        if self.kv_store:
            try:
                entry = await self.kv_store.get(self.service_id)
                if not entry:
                    raise RuntimeError(
                        f"Service '{self.service_id}' not found in KV store. "
                        f"The service may not be registered or has been unregistered."
                    )

                service_data = json.loads(entry.value.decode("utf-8"))

                # Convert functions_description from JSON back to Description objects
                functions_description = {}
                for name, func_data in service_data.get(
                    "functions_description", {}
                ).items():
                    if isinstance(func_data, dict):
                        # Convert JSON dict back to Description object using from_json
                        functions_description[name] = Description.from_json(
                            json.dumps(func_data)
                        )
                    else:
                        # Keep as-is if already Description object (shouldn't happen in practice)
                        functions_description[name] = func_data

                self._service_info = ServiceInfo(
                    service_id=service_data["service_id"],
                    service_name=service_data["service_name"],
                    description=service_data.get("description", ""),
                    functions_description=functions_description,
                )
            except (NotFoundError, KeyNotFoundError) as e:
                raise e
            except Exception as e:
                import traceback

                logger.error(
                    f"Error fetching service info for '{self.service_id}':\n{traceback.format_exc()}"
                )
                raise RuntimeError(
                    f"Failed to fetch service info for '{self.service_id}': {e}"
                )
        return self._service_info

    @property
    def service_info(self) -> ServiceInfo:
        return self._service_info


class NATSRemoteWorker(RemoteWorker):
    """NATS remote worker"""

    def __init__(
        self, backend: "NATSBackend", service_name: str, description: str = "", **kwargs
    ):
        self._backend = backend
        self.nc = None
        self.kv_store = None
        self._service_name = service_name
        self._description = description
        self.timeout = kwargs.get("timeout", 30.0)  # Default timeout for reverse calls

        # Generate service ID using full hash for frontend compatibility
        id_hash = kwargs.get("id_hash")
        if id_hash:
            # Ensure id_hash is a string
            id_hash_str = str(id_hash)
            hash_obj = hashlib.sha256(id_hash_str.encode())
            # Use full hash instead of service_name + short_hash for frontend compatibility
            self._service_id = hash_obj.hexdigest()
        else:
            # For cases without id_hash, generate a full hash from service_name + uuid
            fallback_id = f"{service_name}_{str(uuid.uuid4())[:8]}"
            self._service_id = hashlib.sha256(fallback_id.encode()).hexdigest()

        self.service_subject = f"pantheon.service.{self._service_id}"
        self._functions: Dict[str, Callable] = {}
        self._running = False
        self._subscription = None

        # Auto-register ping function for connection checking
        self.register(self._ping)

    async def _ping(self) -> dict:
        """Ping function for connection checking"""
        return {"status": "ok", "service_id": self._service_id}

    def register(self, func: Callable, **kwargs):
        """Register function"""
        func_name = func.__name__
        self._functions[func_name] = func
        if self._running and self.kv_store:
            asyncio.create_task(self._register_to_kv_store())

    async def run(self):
        """Start worker"""
        if self.nc is None:
            self.nc, _ = await self._backend._get_connection()
            self.kv_store = self._backend._kv

        self._running = True
        await self._register_to_kv_store()

        self._subscription = await self.nc.subscribe(
            self.service_subject, cb=self._handle_request
        )
        logger.info(f"NATS worker: {self.service_subject} registered.")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop worker"""
        self._running = False
        if self._subscription:
            await self._subscription.unsubscribe()
            self._subscription = None

        if self.kv_store:
            try:
                await self.kv_store.delete(self._service_id)
                logger.debug(f"Service {self._service_id} unregistered from KV store")
            except Exception as e:
                logger.error(f"Failed to unregister from KV store: {e}")

    async def _register_to_kv_store(self):
        """Register service information to KV store using JSON"""
        if not self.kv_store:
            return

        try:
            service_info = self.get_service_info()

            # Convert functions_description to JSON-serializable format using built-in to_json()
            functions_description_serializable = {}
            for name, desc in service_info.functions_description.items():
                if hasattr(desc, "to_json"):
                    # Use built-in to_json() method for Description objects
                    functions_description_serializable[name] = json.loads(
                        desc.to_json()
                    )
                else:
                    # Keep as-is for already serializable objects
                    functions_description_serializable[name] = desc

            service_data = {
                "service_id": service_info.service_id,
                "service_name": service_info.service_name,
                "description": service_info.description,
                "functions_description": functions_description_serializable,
                "subject": self.service_subject,
                "registered_at": asyncio.get_event_loop().time(),
            }
            await self.kv_store.put(
                self._service_id, json.dumps(service_data).encode("utf-8")
            )
            logger.debug(
                f"Service {self._service_id} registered to KV store using JSON"
            )
        except Exception as e:
            logger.error(f"Failed to register to KV store: {e}")

    def get_service_info(self) -> ServiceInfo:
        """Get service information"""
        functions_description = {}
        for name, func in self._functions.items():
            try:
                functions_description[name] = parse_func(func)
            except Exception:
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

    def _parse_request_message(self, data: bytes) -> tuple[str, dict, bool]:
        """Parse request message and return (method, parameters, is_json_request)"""
        # Try cloudpickle first (backend format)
        try:
            message: NATSMessage = cloudpickle.loads(data)
            return message.method, message.parameters, False
        except Exception:
            # Fallback to JSON for frontend clients
            try:
                message_data = json.loads(data.decode("utf-8"))
                method = message_data["method"]
                parameters = message_data.get("parameters", {})
                return method, parameters, True
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise Exception("Unable to decode request message")

    async def _handle_request(self, msg):
        """
        This callback is executed by the NATS client for each message.
        It creates a new asyncio task to handle the request concurrently
        and returns immediately, allowing the NATS client to process the
        next message without waiting.
        """
        asyncio.create_task(self._process_and_respond(msg))

    async def _process_and_respond(self, msg):
        """
        Processes a single request, executes the target function, and sends
        the response. This runs as a concurrent task.
        """
        try:
            method, parameters, is_json_request = self._parse_request_message(msg.data)

            if method not in self._functions:
                error_response = {
                    "error": f"Method {method} not found on service {self._service_id}"
                }
                await msg.respond(json.dumps(error_response).encode("utf-8"))
                return

            logger.debug(
                f"NATS worker:{self.service_subject} received function request: {method}"
            )

            # Restore callable parameters using ReverseCallHelper
            processed_parameters = ReverseCallHelper.restore_callables(
                parameters, self.nc, timeout=getattr(self, "timeout", 30.0)
            )

            func = self._functions[method]

            # Use run_func to handle both sync and async functions correctly
            result = await run_func(func, **processed_parameters)

            logger.debug(
                f"NATS worker:{self.service_subject} finished function request: {method}"
            )
            response = {"result": result}

            # Respond in the same format as the request for successful calls
            if is_json_request:
                await msg.respond(json.dumps(response).encode("utf-8"))
            else:
                await msg.respond(cloudpickle.dumps(response))

        except Exception as e:
            error_response = {"error": str(e)}
            await msg.respond(json.dumps(error_response).encode("utf-8"))

    @property
    def service_id(self) -> str:
        return self._service_id

    @property
    def service_name(self) -> str:
        return self._service_name

    @property
    def servers(self) -> List[str]:
        return self._backend.server_urls

    @property
    def functions(self) -> Dict[str, tuple]:
        return {
            name: (func, getattr(func, "__doc__", ""))
            for name, func in self._functions.items()
        }


# ==============================================================================
# Reverse Call Support
# ==============================================================================
# This section provides reverse call functionality as an optional enhancement.
# Based on commits 98b7f46 and 635d1b3 from weize-dev-hypha branch.


class ReverseInvokeError(Exception):
    """Exception raised when reverse invoke fails"""

    pass


class ReverseCallable:
    """
    A callable wrapper that enables reverse calls from worker to client.
    This allows workers to invoke callbacks provided by the client.

    Note: This is an async callable - use with await.
    """

    def __init__(
        self,
        nc: nats.aio.client.Client,
        name: str,
        invoke_id: str,
        parameters: list[str],
        is_async: bool,
        call_timeout: float = 30.0,
    ):
        self.nc = nc
        self.name = name
        self.invoke_id = invoke_id
        self.parameters = parameters
        self.is_async = is_async
        self.call_timeout = call_timeout

    async def invoke(self, params: dict) -> Any:
        """Async invoke the reverse callable"""
        req_payload = {
            "action": "reverse_invoke",
            "name": self.name,
            "parameters": params,
        }
        logger.info(f"[ReverseCall]Invoking reverse callable {self.name}")
        try:
            resp = await self.nc.request(
                self.invoke_id,
                cloudpickle.dumps(req_payload),
                timeout=self.call_timeout,
            )
            res = cloudpickle.loads(resp.data)

            if res.get("status") == "error":
                raise ReverseInvokeError(res.get("message", "Unknown error"))
            return res.get("result")
        except asyncio.TimeoutError:
            raise ReverseInvokeError(f"Reverse call timeout for {self.name}")

    async def __call__(self, *args, **kwargs):
        """
        Make the object async callable.
        Always use with await: result = await reverse_callable(args)
        """
        params = {}
        # Map positional args to parameter names
        if self.parameters:
            for i, param_name in enumerate(self.parameters):
                if i < len(args):
                    params[param_name] = args[i]
        params.update(kwargs)

        return await self.invoke(params)


class ReverseCallHelper:
    """
    Helper class to handle reverse call logic independently.
    Provides utilities for both client and worker sides.
    """

    @staticmethod
    def process_parameters(
        parameters: Dict[str, Any], invoke_id: str
    ) -> tuple[Dict[str, Any], Dict[str, Callable]]:
        """
        Process parameters to detect and wrap callable objects for reverse calls.

        Returns:
            (processed_parameters, reverse_callables):
                - processed_parameters with callables replaced by metadata
                - dict of original callable objects
        """
        reverse_callables = {}
        processed_parameters = {}

        for k, v in parameters.items():
            if callable(v):
                logger.info(
                    f"[ReverseCall]Detected reverse callable parameter: {k} (type: {type(v)})"
                )
                # Store the original callable
                reverse_callables[k] = v
                # Replace with metadata for the worker
                processed_parameters[k] = {
                    "_reverse_callable": True,
                    "name": k,
                    "invoke_id": invoke_id,
                    "parameters": list(inspect.signature(v).parameters.keys()),
                    "is_async": inspect.iscoroutinefunction(v),
                }
            else:
                processed_parameters[k] = v

        return processed_parameters, reverse_callables

    @staticmethod
    async def setup_handler(
        nc: nats.aio.client.Client,
        invoke_id: str,
        reverse_callables: Dict[str, Callable],
    ):
        """
        Set up subscription to handle reverse calls from worker.

        Returns:
            subscription object for cleanup
        """

        async def reverse_call_handler(msg):
            """Handle reverse invoke requests from worker"""
            try:
                req = cloudpickle.loads(msg.data)
                if req.get("action") == "reverse_invoke":
                    func_name = req["name"]
                    func = reverse_callables.get(func_name)
                    if not func:
                        error_response = {
                            "status": "error",
                            "message": f"Reverse callable {func_name} not found",
                        }
                        await msg.respond(cloudpickle.dumps(error_response))
                        return

                    # Execute the callback
                    try:
                        result = await run_func(func, **req["parameters"])
                        response = {
                            "status": "success",
                            "result": result,
                        }
                    except Exception as e:
                        response = {
                            "status": "error",
                            "message": f"{e.__class__.__name__}: {str(e)}",
                        }
                    await msg.respond(cloudpickle.dumps(response))
            except Exception as e:
                logger.error(f"Error in reverse call handler: {e}")

        return await nc.subscribe(invoke_id, cb=reverse_call_handler)

    @staticmethod
    def restore_callables(
        parameters: Dict[str, Any], nc: nats.aio.client.Client, timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        Restore callable parameters from metadata on worker side.

        Args:
            parameters: Parameters dict potentially containing reverse callable metadata
            nc: NATS connection
            timeout: Timeout for reverse calls

        Returns:
            Parameters dict with metadata replaced by ReverseCallable objects
        """
        processed_parameters = {}

        for k, v in parameters.items():
            if isinstance(v, dict) and v.get("_reverse_callable"):
                # Create a ReverseCallable object
                processed_parameters[k] = ReverseCallable(
                    nc=nc,
                    name=v["name"],
                    invoke_id=v["invoke_id"],
                    parameters=v["parameters"],
                    is_async=v["is_async"],
                    call_timeout=timeout,
                )
            else:
                processed_parameters[k] = v

        return processed_parameters


class ReverseCallContext:
    """
    Context manager for reverse call operations.
    Provides clean setup and teardown of reverse call infrastructure.
    """

    def __init__(self, nc: nats.aio.client.Client, parameters: Dict[str, Any]):
        self.nc = nc
        self.original_parameters = parameters
        self.invoke_id = str(uuid.uuid4())
        self.subscription = None
        self.processed_parameters = None
        self.reverse_callables = None

    async def __aenter__(self):
        """Setup reverse call infrastructure"""
        # Process parameters
        self.processed_parameters, self.reverse_callables = (
            ReverseCallHelper.process_parameters(
                self.original_parameters, self.invoke_id
            )
        )

        # Setup handler if there are callables
        if self.reverse_callables:
            self.subscription = await ReverseCallHelper.setup_handler(
                self.nc, self.invoke_id, self.reverse_callables
            )

        return self.processed_parameters

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup reverse call infrastructure"""
        if self.subscription:
            await self.subscription.unsubscribe()
        return False
