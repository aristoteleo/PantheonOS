"""
NATS Remote Backend Implementation
Integrates RPC calls and streaming functionality using Core NATS + Optional JetStream KV
"""

import asyncio
import hashlib
import inspect
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import cloudpickle
import nats
from nats.js.errors import NotFoundError, KeyNotFoundError
from funcdesc import parse_func, Description

from pantheon.utils.log import logger
from pantheon.utils.misc import run_func
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


def _format_subject(prefix: str, suffix: str) -> str:
    """Format NATS subject with optional prefix"""
    if prefix:
        return f"{prefix}.{suffix}"
    return suffix


def _format_bucket(prefix: str, bucket_name: str) -> str:
    """Format KV bucket name with optional prefix"""
    if prefix:
        safe_prefix = prefix.replace(".", "-")
        return f"{safe_prefix}-{bucket_name}"
    return bucket_name

class NATSStreamChannel(StreamChannel):
    """NATS Core stream channel implementation using native pub/sub for high performance and low latency"""

    def __init__(self, stream_id: str, stream_type: StreamType, backend: "NATSBackend"):
        self._stream_id = stream_id
        self._stream_type = stream_type
        self._backend = backend
        self._closed = False
        
        # Use subject prefix if available
        prefix = getattr(backend, "subject_prefix", "")
        self._subject = _format_subject(prefix, f"pantheon.stream.{stream_id}")

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
        """Subscribe to NATS stream messages."""
        if self._closed:
            raise RuntimeError("StreamChannel is closed")

        nc, _ = await self._backend._get_connection()

        async def message_handler(msg):
            try:
                # Parse JSON message
                payload = json.loads(msg.data.decode("utf-8"))
                # Convert to StreamMessage object
                from .base import StreamMessage

                stream_message = StreamMessage.from_dict(payload)
                # Call callback function - use run_func to handle sync/async automatically
                await run_func(callback, stream_message)
            except Exception as e:
                logger.error(f"Error processing NATS stream message: {e}")

        # Subscribe to NATS subject
        subscription = await nc.subscribe(self._subject, cb=message_handler)
        subscription_id = str(id(subscription))

        # Store subscription for later unsubscribe
        if not hasattr(self, "_subscriptions"):
            self._subscriptions = {}
        self._subscriptions[subscription_id] = subscription

        logger.debug(f"NATS stream subscribed: {self._subject} -> {subscription_id}")
        return subscription_id

    async def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from NATS stream messages."""
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
    """NATS remote backend - Core NATS streaming + Optional JetStream KV storage"""

    def __init__(self, server_urls: list[str], subject_prefix: str = "", **nats_kwargs):
        self.server_urls = server_urls or ["nats://localhost:4222"]
        self.subject_prefix = subject_prefix
        self.nats_kwargs = nats_kwargs
        self._nc: nats.aio.client.Client = None
        self._js = None  # Only used for KV store
        self._kv = None  # JetStream KV store

        # ✅ Control JetStream initialization via environment variable
        # Default: enabled (true) for backward compatibility
        self.enable_jetstream = os.getenv('NATS_ENABLE_JETSTREAM', 'true').lower() == 'true'
        if not self.enable_jetstream:
            logger.info("JetStream disabled via NATS_ENABLE_JETSTREAM=false")

        # Core NATS stream management
        self._streams: Dict[str, NATSStreamChannel] = {}

    async def _get_connection(self):
        """Get NATS connection with enhanced keepalive for long-running pods"""
        if not self._nc:
            # Build connection parameters with keepalive config
            connect_params = dict(self.nats_kwargs)

            # Set aggressive ping interval to detect stale connections.
            # LB/proxy idle timeout is often 30-60s on public internet;
            # 20s ensures a packet is sent before the LB silently drops the connection.
            if 'ping_interval' not in connect_params:
                connect_params['ping_interval'] = 20

            # 3 missed PONGs = ~60s silence before declaring disconnect.
            # Raised from 2 to tolerate higher/variable public internet latency.
            if 'max_outstanding_pings' not in connect_params:
                connect_params['max_outstanding_pings'] = 3

            # Enable infinite reconnection attempts for long-running pods
            if 'max_reconnect_attempts' not in connect_params:
                connect_params['max_reconnect_attempts'] = -1  # Infinite retries

            if 'reconnect_time_wait' not in connect_params:
                connect_params['reconnect_time_wait'] = 2

            # Default connect_timeout is 2s which is too tight for public internet;
            # 5s gives enough headroom without delaying failure detection unnecessarily.
            if 'connect_timeout' not in connect_params:
                connect_params['connect_timeout'] = 5

            # Use callbacks to handle connection events instead of manual flush health checks.
            # This avoids the asyncio.InvalidStateError race condition in nats.py _process_pong()
            # which is triggered when an outer wait_for cancels flush() while a PONG is in-flight.
            if 'error_cb' not in connect_params:
                async def _error_cb(e):
                    # asyncio.InvalidStateError is a known nats.py library bug:
                    # _process_pong() calls future.set_result() on an already-cancelled future
                    # when flush() is wrapped in an external wait_for. Safe to ignore.
                    if isinstance(e, asyncio.InvalidStateError):
                        return
                    logger.warning(f"[NATS] Connection error: {e}")
                connect_params['error_cb'] = _error_cb

            if 'disconnected_cb' not in connect_params:
                async def _disconnected_cb():
                    logger.warning("[NATS] Disconnected from server")
                connect_params['disconnected_cb'] = _disconnected_cb

            if 'reconnected_cb' not in connect_params:
                async def _reconnected_cb():
                    # self._nc is guaranteed to be set here because reconnected_cb
                    # is only triggered after the initial connect() returns and
                    # the client has already assigned self._nc.
                    url = self._nc.connected_url if self._nc else "unknown"
                    logger.info(f"[NATS] Reconnected to {url}")
                connect_params['reconnected_cb'] = _reconnected_cb

            logger.info(f"[NATS] Connecting to {self.server_urls} with ping_interval={connect_params['ping_interval']}s")
            self._nc = await nats.connect(servers=self.server_urls, **connect_params)

            # ✅ Initialize JetStream only if enabled
            if self.enable_jetstream:
                # Initialize JetStream only for KV store
                try:
                    self._js = self._nc.jetstream()

                    # Determine dynamic bucket name based on subject_prefix
                    # FIX: Force use of global 'pantheon-service' bucket for service discovery
                    # This ensures frontend and agents look in the same place despite subject prefix
                    bucket_name = "pantheon-service"

                    # Create KV bucket for service discovery
                    try:
                        self._kv = await self._js.key_value(bucket_name)
                    except Exception:
                        try:
                            self._kv = await self._js.create_key_value(
                                bucket=bucket_name
                            )
                            logger.debug(f"Created NATS KV bucket: {bucket_name}")
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
            else:
                # ✅ JetStream disabled - skip initialization
                logger.info(
                    "Skipping JetStream initialization (NATS_ENABLE_JETSTREAM=false), "
                    "KV store will not be available"
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
        # Pass subject_prefix to service
        kwargs["subject_prefix"] = self.subject_prefix
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
        timeout: float | None = None,
        subject_prefix: str = "",
        **kwargs,
    ):
        self.nc = nc
        self.service_id = service_id
        self.kv_store = kv_store

        # Use provided timeout or get from settings
        if timeout is not None:
            self.timeout = timeout
        else:
            try:
                from pantheon.settings import get_settings
                self.timeout = get_settings().tool_timeout
            except Exception:
                self.timeout = 3600

        self.service_subject = _format_subject(subject_prefix, f"pantheon.service.{service_id}")

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

        # Use provided timeout or get from settings
        if "timeout" in kwargs:
            self.timeout = kwargs["timeout"]
        else:
            try:
                from pantheon.settings import get_settings
                self.timeout = get_settings().tool_timeout
            except Exception:
                self.timeout = 3600

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

        # Use subject prefix if available from backend
        prefix = getattr(backend, "subject_prefix", "")
        self.service_subject = _format_subject(prefix, f"pantheon.service.{self._service_id}")
        self._functions: Dict[str, Callable] = {}
        self._running = False
        self._subscription = None
        self._activity_callback: Optional[Callable[[], dict]] = None

        # Auto-register ping function for connection checking
        self.register(self._ping)

    def set_activity_callback(self, callback: Callable[[], dict]):
        """Register a callback that returns activity status for _ping responses."""
        self._activity_callback = callback

    async def _ping(self) -> dict:
        """Ping function for connection checking"""
        from pantheon import __version__
        result = {"status": "ok", "service_id": self._service_id, "version": __version__}
        if self._activity_callback:
            try:
                result.update(self._activity_callback())
            except Exception:
                pass
        return result

    def register(self, func: Callable, **kwargs):
        """Register function"""
        func_name = func.__name__
        self._functions[func_name] = func
        if self._running and self.kv_store:
            asyncio.create_task(self._register_to_kv_store())

    async def run(self):
        """Start worker. Connection health is managed by NATS client's built-in ping/reconnect."""
        logger.info(f"[NATSWorker.run] Starting worker for subject: {self.service_subject}")
        if self.nc is None:
            logger.info(f"[NATSWorker.run] Connecting to NATS: {self._backend.server_urls}")
            self.nc, _ = await self._backend._get_connection()
            self.kv_store = self._backend._kv
            logger.info(f"[NATSWorker.run] NATS connected: {self.nc.connected_url}")

        self._running = True

        # KV registration and subject subscription are independent — run in parallel.
        self._subscription, _ = await asyncio.gather(
            self.nc.subscribe(self.service_subject, cb=self._handle_request),
            self._register_to_kv_store(),
        )
        logger.info(f"NATS worker: {self.service_subject} registered.")

        # Signal that the worker is ready to accept requests
        if hasattr(self, '_on_ready') and self._on_ready:
            self._on_ready.set()

        # NATS client handles health via ping_interval=20s / max_outstanding_pings=3.
        # After 3 missed PONGs (~60s silence) it auto-disconnects and reconnects,
        # replaying all subscriptions automatically. No manual monitor task needed.
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
            import traceback
            error_msg = f"Error processing {method if 'method' in locals() else 'request'}: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
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
