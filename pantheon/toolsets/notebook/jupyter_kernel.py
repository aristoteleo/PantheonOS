"""Jupyter Client Kernel ToolSet - Standard Jupyter kernel implementation using jupyter_client"""

import asyncio
import os
import re
import time
import urllib.parse
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import nbformat.v4

import zmq.asyncio
from jupyter_client import AsyncKernelManager
from jupyter_client.asynchronous import AsyncKernelClient
from jupyter_client.session import Session

from pantheon.remote.backend.base import RemoteBackend, StreamMessage, StreamType
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
from pantheon.utils.misc import run_func

# Terminal control character processing (nbclient-style)
# Reference: https://github.com/jupyter/nbclient/blob/main/nbclient/client.py

# Regex patterns for terminal control characters
_RGX_CARRIAGERETURN = re.compile(r".*\r(?=[^\n])")
_RGX_BACKSPACE = re.compile(r"[^\n]\b")


class KernelStatus(Enum):
    """Kernel execution status"""

    IDLE = "idle"
    BUSY = "busy"
    STARTING = "starting"
    DEAD = "dead"


@dataclass
class JupyterMessage:
    """Standard Jupyter message structure"""

    msg_type: str
    content: dict
    header: dict
    metadata: Optional[dict] = None
    parent_header: Optional[dict] = None
    buffers: Optional[List[bytes]] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.parent_header is None:
            self.parent_header = {}
        if self.buffers is None:
            self.buffers = []

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionInfo:
    """Kernel session information"""

    session_id: str
    kernel_spec: str
    status: KernelStatus
    created_at: str
    execution_count: int = 0


class IOPubEventBus(ABC):
    """Abstract IOPub event publishing system"""

    @abstractmethod
    async def publish(
        self, session_id: str, message: JupyterMessage, metadata: Optional[dict] = None
    ) -> None:
        """Publish message to all subscribers of specified session"""
        pass

    @abstractmethod
    async def subscribe(
        self, session_id: str, client_id: str, callback: Callable
    ) -> str:
        """Subscribe to IOPub messages for session"""
        pass

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from messages"""
        pass


class RemoteIOPubEventBus(IOPubEventBus):
    """
    IOPub event bus implementation based on unified remote backend

    This class is primarily designed for PUBLISHING IOPub messages from backend to frontend.
    For SUBSCRIBING to messages, use frontend StreamChannel.subscribe() directly for better performance.
    """

    def __init__(self, remote_backend: RemoteBackend):
        self.remote_backend = remote_backend
        self.stream_channels: Dict[str, Any] = {}  # stream_id -> StreamChannel

    async def publish(
        self, session_id: str, message: JupyterMessage, metadata: Optional[dict] = None
    ) -> None:
        """Convert Jupyter message to stream message and publish with optional metadata"""
        try:
            # Encode session_id to avoid NATS subject special characters (. : * >)
            # NATS treats . as token separator in subjects, so "chat:analysis.ipynb"
            # would be parsed incorrectly. URL encoding ensures proper handling.
            safe_session_id = urllib.parse.quote(session_id, safe="")
            stream_id = f"notebook_iopub_{safe_session_id}"

            # Get or create stream channel
            if stream_id not in self.stream_channels:
                self.stream_channels[
                    stream_id
                ] = await self.remote_backend.get_or_create_stream(
                    stream_id, StreamType.NOTEBOOK
                )

            stream_channel = self.stream_channels[stream_id]

            # Send Jupyter message directly (simplified structure)
            jupyter_message_dict = make_json_serializable(message.to_dict())

            # Prepare stream metadata (merge default + provided metadata)
            stream_metadata = {
                "source": "jupyter_kernel",
            }
            if metadata:
                stream_metadata.update(metadata)

            stream_message = StreamMessage(
                type=StreamType.NOTEBOOK,
                session_id=session_id,
                timestamp=time.time(),
                data=jupyter_message_dict,  # Direct Jupyter message
                metadata=stream_metadata,  # Enhanced metadata support
            )

            # Publish to stream channel
            await stream_channel.publish(stream_message)
            logger.debug(
                f"Published IOPub message to stream {stream_id}: {message.msg_type}"
            )

        except Exception as e:
            logger.error(
                f"Failed to publish IOPub message for session {session_id}: {e}"
            )
            raise

    async def subscribe(
        self, session_id: str, client_id: str, callback: Callable
    ) -> str:
        """Backend subscription not supported - use frontend StreamChannel.subscribe()"""
        raise NotImplementedError("Use frontend StreamChannel.subscribe() instead")

    async def unsubscribe(self, subscription_id: str) -> bool:
        """Backend unsubscription not supported - use frontend StreamChannel.unsubscribe()"""
        raise NotImplementedError("Use frontend StreamChannel.unsubscribe() instead")

    async def cleanup(self):
        """Clean up all stream channels"""
        try:
            # Close all stream channels
            for stream_id, channel in self.stream_channels.items():
                try:
                    await channel.close()
                    logger.debug(f"Closed stream channel: {stream_id}")
                except Exception as e:
                    logger.warning(f"Error closing stream channel {stream_id}: {e}")

            self.stream_channels.clear()
            logger.info("UnifiedRemoteIOPubEventBus cleanup completed")

        except Exception as e:
            logger.error(f"Error during IOPub event bus cleanup: {e}")


class KernelListener:
    """Unified Kernel IOPub message listener - uses single ZMQ poller to monitor all kernels"""

    def __init__(self):
        """Initialize unified listener"""
        self.context = zmq.asyncio.Context()
        self.poller = zmq.asyncio.Poller()
        self.socket_to_session: Dict[zmq.Socket, str] = {}
        self.session_handlers: Dict[str, Callable] = {}
        self.session_sockets: Dict[str, zmq.Socket] = {}
        self.is_running = False
        self.listener_task: Optional[asyncio.Task] = None
        # Official Jupyter session for message parsing (disable signature validation for streaming)
        self.jupyter_session = Session(key=b"", auth=None)

    async def add_kernel(
        self, session_id: str, connection_info: dict, message_handler: Callable
    ) -> bool:
        """Add kernel to unified listener"""
        try:
            # Create SUB socket connected to kernel's IOPub port
            socket = self.context.socket(zmq.SUB)
            socket.connect(
                f"tcp://{connection_info['ip']}:{connection_info['iopub_port']}"
            )
            socket.subscribe(b"")  # Subscribe to all messages

            # Register to poller
            self.poller.register(socket, zmq.POLLIN)

            # Store mapping relationships
            self.socket_to_session[socket] = session_id
            self.session_handlers[session_id] = message_handler
            self.session_sockets[session_id] = socket

            logger.info(f"Added kernel {session_id} to unified listener")

            # If this is the first kernel, start unified listening
            if not self.is_running:
                await self.start_listening()

            return True

        except Exception as e:
            logger.error(f"Failed to add kernel {session_id} to unified listener: {e}")
            return False

    async def remove_kernel(self, session_id: str) -> bool:
        """Remove kernel from unified listener"""
        try:
            if session_id not in self.session_sockets:
                return True

            socket = self.session_sockets[session_id]

            # Remove from poller
            self.poller.unregister(socket)

            # Clean up mapping relationships
            del self.socket_to_session[socket]
            del self.session_handlers[session_id]
            del self.session_sockets[session_id]

            # Close socket
            socket.close()

            logger.info(f"Removed kernel {session_id} from unified listener")

            # If no kernels left, stop listening
            if not self.session_sockets and self.is_running:
                await self.stop_listening()

            return True

        except Exception as e:
            logger.error(
                f"Failed to remove kernel {session_id} from unified listener: {e}"
            )
            return False

    async def start_listening(self):
        """Start unified listening task"""
        if self.is_running:
            return

        self.is_running = True
        self.listener_task = asyncio.create_task(self._listen_loop())
        logger.info("Started unified kernel listener")

    async def stop_listening(self):
        """Stop unified listening task"""
        if not self.is_running:
            return

        self.is_running = False

        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
            self.listener_task = None

        logger.info("Stopped unified kernel listener")

    async def _listen_loop(self):
        """Unified listening loop - true event-driven, zero polling!"""
        logger.info("Starting unified IOPub listening loop")

        try:
            while self.is_running:
                # If no sessions registered yet, wait before checking again
                # This prevents busy-waiting while allowing the listener to persist
                if not self.session_sockets:
                    await asyncio.sleep(0.5)  # Wait 500ms before checking again
                    continue

                try:
                    # Event-driven waiting with periodic refresh for dynamic socket registration
                    # timeout=100ms ensures newly registered sockets are picked up within 100ms,
                    # preventing race conditions when add_kernel() is called during poll()
                    events = await self.poller.poll(timeout=100)

                    # Process all sockets with messages
                    for socket, event in events:
                        if event & zmq.POLLIN:
                            await self._handle_socket_message(socket)

                except asyncio.CancelledError:
                    logger.info("Unified listener loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in unified listener loop: {e}")
                    await asyncio.sleep(0.01)  # Brief pause before retry

        finally:
            logger.info("Unified IOPub listening loop ended")

    async def _handle_socket_message(self, socket: zmq.Socket):
        """Handle individual socket message"""
        try:
            # Receive ZMQ multipart message
            multipart_msg = await socket.recv_multipart(flags=zmq.NOBLOCK)

            # Skip empty or malformed messages
            if not multipart_msg or len(multipart_msg) < 2:
                logger.info(
                    f"Skipping malformed multipart message with {len(multipart_msg) if multipart_msg else 0} parts"
                )
                return

            # Parse to Jupyter message
            jupyter_msg = self._parse_zmq_message(multipart_msg)

            # Skip error messages from parsing (they're already logged)
            if (
                jupyter_msg.msg_type == "error"
                and "Failed to parse message" in jupyter_msg.content.get("error", "")
            ):
                return

            # Get corresponding session_id and handler
            session_id = self.socket_to_session.get(socket)
            if session_id and session_id in self.session_handlers:
                handler = self.session_handlers[session_id]
                # Handle both sync and async handlers
                if asyncio.iscoroutinefunction(handler):
                    await handler(session_id, jupyter_msg)
                else:
                    await run_func(handler, session_id, jupyter_msg)

        except zmq.Again:
            # No message to read, normal case
            logger.info("No message to read, normal case")
        except Exception as e:
            session_id = self.socket_to_session.get(socket, "unknown")
            logger.error(f"Error handling message for session {session_id}: {e}")

    def _parse_zmq_message(self, multipart_msg: List[bytes]) -> JupyterMessage:
        """
        Parse ZMQ multipart message using official Jupyter Session API.
        Simplified from 175+ lines to ~20 lines using jupyter_client.session.Session.
        """
        try:
            # Use the same approach as jupyter_client:
            # 1. feed_identities() removes topic prefixes and returns standard format
            # 2. deserialize() processes the standard format
            _, processed_msg = self.jupyter_session.feed_identities(
                multipart_msg, copy=True
            )
            msg_dict = self.jupyter_session.deserialize(
                processed_msg, content=True, copy=True
            )

            logger.debug(
                f"Successfully parsed message: {msg_dict.get('msg_type', 'unknown')}"
            )
            return JupyterMessage(
                msg_type=msg_dict.get("msg_type", "unknown"),
                content=msg_dict.get("content", {}),
                header=msg_dict.get("header", {}),
                parent_header=msg_dict.get("parent_header", {}),
                metadata=msg_dict.get("metadata", {}),
                buffers=msg_dict.get("buffers", []),
            )
        except Exception as e:
            logger.error(f"Failed to parse ZMQ message: {e}")
            return self._create_error_message(f"Parse failed: {e}")

    def _create_error_message(self, error_msg: str) -> JupyterMessage:
        """Create a JupyterMessage for parsing errors"""
        return JupyterMessage(
            msg_type="parse_error",
            content={"error": error_msg},
            header={"msg_type": "parse_error"},
            parent_header={},
            metadata={},
        )

    async def cleanup(self):
        """Clean up resources"""
        await self.stop_listening()

        # Close all sockets
        for socket in list(self.session_sockets.values()):
            socket.close()

        # Clear all mappings
        self.socket_to_session.clear()
        self.session_handlers.clear()
        self.session_sockets.clear()

        # Close context
        self.context.term()


def make_json_serializable(obj):
    """
    Convert datetime and other non-serializable objects to JSON-serializable format.
    Optimized for Jupyter message processing - handles datetime objects in headers.
    """
    # Fast path for already serializable basic types
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Handle container types recursively
    elif isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]

    # Handle datetime objects (main use case in Jupyter messages)
    elif isinstance(obj, datetime):
        return obj.isoformat()

    # Handle other common non-serializable types
    elif hasattr(obj, "isoformat"):  # date, time objects
        return obj.isoformat()
    elif hasattr(obj, "tolist"):  # numpy arrays
        return obj.tolist()
    elif hasattr(obj, "__dict__") and hasattr(obj, "__class__"):
        # Only convert custom objects, not built-in types
        if obj.__class__.__module__ not in ("builtins", "__builtin__"):
            return make_json_serializable(obj.__dict__)

    # Fallback: convert to string for any remaining non-serializable objects
    return str(obj)


class JupyterKernelToolSet(ToolSet):
    """Standard Jupyter kernel implementation using jupyter_client"""

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        use_unified_listener: bool = True,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.workdir = workdir or os.getcwd()

        # Event bus will be set by parent toolset during setup
        self.event_bus: Optional[IOPubEventBus] = None

        # Configure listening mode
        self.use_unified_listener = use_unified_listener

        # Kernel management
        self.kernel_managers: Dict[str, AsyncKernelManager] = {}
        self.clients: Dict[str, AsyncKernelClient] = {}
        self.sessions: Dict[str, SessionInfo] = {}
        self.iopub_tasks: Dict[str, asyncio.Task] = {}

        # Track execution metadata for IOPub message injection
        self.msg_metadata_mapping: Dict[str, dict] = {}  # msg_id -> execution_metadata

        # Unified listener (created only when unified listening mode is enabled)
        self.unified_listener: Optional[KernelListener] = None
        if self.use_unified_listener:
            self.unified_listener = KernelListener()
            logger.debug("Initialized unified kernel listener")

    async def _handle_iopub_message(self, session_id: str, jupyter_msg: JupyterMessage):
        """Unified IOPub message handler - Separate metadata flow"""
        try:
            parent_msg_id = (
                jupyter_msg.parent_header.get("msg_id")
                if jupyter_msg.parent_header
                else None
            )

            # Prepare stream metadata (separate from JupyterMessage.metadata)
            stream_metadata = {}
            if parent_msg_id and parent_msg_id in self.msg_metadata_mapping:
                execution_metadata = self.msg_metadata_mapping[parent_msg_id]
                stream_metadata.update(execution_metadata)
                logger.debug(
                    f"Prepared stream metadata for IOPub message {jupyter_msg.msg_type}: {list(execution_metadata.keys())}"
                )

            # Forward to event bus with separate metadata
            if self.event_bus:
                await self.event_bus.publish(session_id, jupyter_msg, stream_metadata)

        except Exception as e:
            logger.error(f"Error handling IOPub message for session {session_id}: {e}")

    @tool
    async def create_session(
        self, kernel_spec: str = "python3", kernel_session_id: str = None
    ) -> dict:
        """Create new kernel session"""
        try:
            if kernel_session_id is None:
                kernel_session_id = str(uuid.uuid4())

            # Try to create kernel manager with the specified kernel, fallback to default
            try:
                km = AsyncKernelManager(kernel_name=kernel_spec)
            except Exception as e:
                logger.warning(
                    f"Kernel '{kernel_spec}' not available, using default: {e}"
                )
                # Use default kernel (no kernel_name specified)
                km = AsyncKernelManager()

            # Start kernel in specified working directory
            await km.start_kernel(cwd=self.workdir)

            # Wait for kernel to be ready
            kc = km.client()
            # start_channels() might not be async in some versions
            try:
                result = kc.start_channels()
                if result is not None:
                    await result
            except TypeError:
                # start_channels() is not async, call it directly
                kc.start_channels()

            # Wait for kernel to be ready
            try:
                await kc.wait_for_ready(timeout=30)
            except RuntimeError as e:
                await km.shutdown_kernel()
                return {"success": False, "error": f"Kernel failed to start: {e}"}

            # Store references
            self.kernel_managers[kernel_session_id] = km
            self.clients[kernel_session_id] = kc

            # Create session info
            session_info = SessionInfo(
                session_id=kernel_session_id,
                kernel_spec=kernel_spec,
                status=KernelStatus.IDLE,
                created_at=datetime.now(timezone.utc).isoformat(),
                execution_count=0,
            )
            self.sessions[kernel_session_id] = session_info

            # Start IOPub monitoring
            if self.event_bus:
                await self._setup_iopub_monitoring(kernel_session_id, km, kc)

            logger.info(f"Created Jupyter kernel session: {kernel_session_id}")

            return {
                "success": True,
                "session_id": kernel_session_id,
                "kernel_spec": kernel_spec,
                "status": session_info.status.value,
                "created_at": session_info.created_at,
                "streaming_enabled": self.event_bus is not None,
            }

        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def execute_request(
        self,
        code: str,
        session_id: str,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: Optional[dict] = None,
        allow_stdin: bool = True,
        stop_on_error: bool = True,
        execution_metadata: Optional[dict] = None,
    ) -> dict:
        """Execute code in kernel session"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        client = self.clients[session_id]
        session_info = self.sessions[session_id]

        try:
            # Update kernel status
            session_info.status = KernelStatus.BUSY

            # Execute code
            msg_id = client.execute(
                code,
                silent=silent,
                store_history=store_history,
                user_expressions=user_expressions or {},
                allow_stdin=allow_stdin,
                stop_on_error=stop_on_error,
            )

            # Store execution metadata for IOPub message injection
            if execution_metadata:
                self.msg_metadata_mapping[msg_id] = execution_metadata.copy()
                logger.debug(
                    f"Stored execution metadata for {msg_id[:8]}: {list(execution_metadata.keys())}"
                )

            # Wait for execution reply - handle both sync and async versions
            try:
                reply = client.get_shell_msg(timeout=60)
                # If get_shell_msg is async, it returns a coroutine
                if hasattr(reply, "__await__"):
                    reply = await reply
            except TypeError:
                # Fallback to async version
                reply = await client.get_shell_msg(timeout=60)

            # Update execution count
            if store_history and reply["content"].get("status") == "ok":
                execution_count = reply["content"].get("execution_count")
                if execution_count:
                    session_info.execution_count = execution_count

            # Update kernel status
            session_info.status = KernelStatus.IDLE

            # Clean the reply to make it JSON serializable
            clean_reply = make_json_serializable(reply)

            # Collect IOPub messages to get actual output and timing information
            iopub_messages = []
            execution_timing = {}
            try:
                # Collect IOPub messages until kernel is idle
                while True:
                    try:
                        # Get IOPub message with timeout (always async since we use AsyncKernelClient)
                        iopub_msg = await client.get_iopub_msg(timeout=1)

                        # Clean and store the message
                        clean_iopub_msg = make_json_serializable(iopub_msg)
                        iopub_messages.append(clean_iopub_msg)

                        # Extract timing information and check for completion from cleaned message
                        # This ensures all downstream consumers get consistent string timestamps
                        msg_type = None
                        execution_state = None

                        if isinstance(clean_iopub_msg, dict):
                            header = clean_iopub_msg.get("header", {})
                            content = clean_iopub_msg.get("content", {})

                            if isinstance(header, dict):
                                msg_type = header.get("msg_type")
                                timestamp = header.get("date")

                                if msg_type == "status" and isinstance(content, dict):
                                    execution_state = content.get("execution_state")
                                    if execution_state == "busy" and timestamp:
                                        execution_timing["iopub.status.busy"] = (
                                            timestamp
                                        )
                                    elif execution_state == "idle" and timestamp:
                                        execution_timing["iopub.status.idle"] = (
                                            timestamp
                                        )
                                elif msg_type == "execute_input" and timestamp:
                                    execution_timing["iopub.execute_input"] = timestamp

                        # Stop when kernel becomes idle (execution finished)
                        if msg_type == "status" and execution_state == "idle":
                            break
                    except Exception:
                        # Timeout or no more messages - execution finished
                        break
            except Exception as e:
                logger.debug(f"Error collecting IOPub messages: {e}")

            # Add shell reply timing (with type safety)
            if isinstance(clean_reply, dict):
                header = clean_reply.get("header", {})
                if isinstance(header, dict):
                    shell_timestamp = header.get("date")
                    if shell_timestamp:
                        execution_timing["shell.execute_reply"] = shell_timestamp

            logger.debug(
                f"Collected {len(iopub_messages)} IOPub messages and timing info for execution"
            )
            for i, msg in enumerate(iopub_messages):
                if isinstance(msg, dict):
                    header = msg.get("header", {})
                    if isinstance(header, dict):
                        msg_type = header.get("msg_type", "unknown")
                    else:
                        msg_type = "unknown"
                    content_preview = str(msg.get("content", {}))[:100]
                    logger.debug(f"  IOPub {i}: {msg_type} - {content_preview}")

            # Generate frontend-compatible outputs from IOPub messages (with type safety)
            execution_count = None
            if isinstance(clean_reply, dict):
                content = clean_reply.get("content", {})
                if isinstance(content, dict):
                    exec_count = content.get("execution_count")
                    if isinstance(exec_count, int):
                        execution_count = exec_count

            outputs = self._generate_outputs_from_iopub(iopub_messages, execution_count)

            # Return standard nbformat-compatible data for internal use
            # This matches what notebook_contents.py expects
            return {
                "success": True,
                "outputs": outputs,  # Standard nbformat outputs
                "execution_count": execution_count,
                "metadata": {
                    "execution": execution_timing  # Standard nbformat metadata structure
                },
                "error": None,  # Consistent error field
            }

        except Exception as e:
            session_info.status = KernelStatus.IDLE
            logger.error(f"Execute request failed for session {session_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "outputs": [],
                "execution_count": None,
            }

    @tool
    async def list_sessions(self) -> dict:
        """List active kernel sessions"""
        try:
            sessions = []
            for session_id, session_info in self.sessions.items():
                sessions.append(
                    {
                        "session_id": session_id,
                        "kernel_spec": session_info.kernel_spec,
                        "status": session_info.status.value,
                        "created_at": session_info.created_at,
                        "execution_count": session_info.execution_count,
                    }
                )

            return {"success": True, "sessions": sessions}
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def shutdown_session(self, session_id: str) -> dict:
        """Shutdown kernel session"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            # Stop IOPub monitoring
            if self.use_unified_listener and self.unified_listener:
                # Remove kernel from unified listener
                await self.unified_listener.remove_kernel(session_id)
            elif session_id in self.iopub_tasks:
                # Stop individual listening task
                self.iopub_tasks[session_id].cancel()
                del self.iopub_tasks[session_id]

            # Shutdown kernel
            km = self.kernel_managers[session_id]
            kc = self.clients[session_id]

            kc.stop_channels()
            await km.shutdown_kernel()

            # Clean up
            del self.kernel_managers[session_id]
            del self.clients[session_id]
            del self.sessions[session_id]

            logger.info(f"Shutdown kernel session: {session_id}")

            return {"success": True, "session_id": session_id}

        except Exception as e:
            logger.error(f"Failed to shutdown session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def restart_session(self, session_id: str) -> dict:
        """Restart kernel session or create new one if not found"""

        # First, try to restart existing session
        if session_id in self.sessions:
            try:
                session_info = self.sessions[session_id]
                session_info.status = KernelStatus.STARTING

                # Step 1: Clean up old IOPub listener BEFORE restart
                # This is critical - the old socket must be removed before restarting
                if self.use_unified_listener and self.unified_listener:
                    await self.unified_listener.remove_kernel(session_id)
                    logger.debug(
                        f"Removed kernel {session_id} from unified listener before restart"
                    )
                elif session_id in self.iopub_tasks:
                    self.iopub_tasks[session_id].cancel()
                    del self.iopub_tasks[session_id]
                    logger.debug(
                        f"Cancelled IOPub task for {session_id} before restart"
                    )

                # Step 2: Restart kernel
                km = self.kernel_managers[session_id]
                kc = self.clients[session_id]

                await km.restart_kernel()
                logger.debug(f"Kernel {session_id} restarted successfully")

                # Step 3: Wait for restarted kernel to be ready
                try:
                    await kc.wait_for_ready(timeout=30)
                    logger.debug(f"Kernel {session_id} is ready after restart")
                except RuntimeError as e:
                    logger.error(
                        f"Kernel {session_id} failed to ready after restart: {e}"
                    )
                    session_info.status = KernelStatus.DEAD
                    return {
                        "success": False,
                        "error": f"Kernel failed to be ready after restart: {e}",
                    }

                # Step 4: Re-setup IOPub monitoring after restart
                # This establishes the new IOPub connection with the restarted kernel
                if self.event_bus:
                    await self._setup_iopub_monitoring(session_id, km, kc)
                    logger.debug(f"IOPub monitoring re-established for {session_id}")

                # Step 5: Reset execution state
                session_info.execution_count = 0
                session_info.status = KernelStatus.IDLE

                logger.info(f"Restarted existing kernel session: {session_id}")
                return {"success": True, "session_id": session_id}

            except Exception as e:
                logger.error(f"Failed to restart existing session {session_id}: {e}")
                # Update session status if it exists
                if session_id in self.sessions:
                    self.sessions[session_id].status = KernelStatus.IDLE
                return {"success": False, "error": str(e)}

        # If session doesn't exist (e.g., after redeployment), create a new one with the same ID
        else:
            logger.info(
                f"Session {session_id} not found, creating new session with same ID"
            )
            result = await self.create_session(kernel_session_id=session_id)

            if result["success"]:
                logger.info(
                    f"Successfully created new kernel session with preserved ID: {session_id}"
                )

            return result

    @tool
    async def interrupt_session(self, session_id: str) -> dict:
        """Interrupt kernel session"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            km = self.kernel_managers[session_id]
            km.interrupt_kernel()

            session_info = self.sessions[session_id]
            session_info.status = KernelStatus.IDLE

            logger.info(f"Interrupted kernel session: {session_id}")

            return {"success": True, "session_id": session_id}

        except Exception as e:
            logger.error(f"Failed to interrupt session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def get_variables(self, session_id: str) -> dict:
        """Get variables from kernel session using %whos magic command"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session {session_id} not found"}

        # Use standard IPython %whos magic command - the only standard way
        # This matches VS Code notebook and JupyterLab behavior exactly
        # Note: %whos excludes private variables by design (standard behavior)
        variables_code = "%whos"

        try:
            # Execute using standard magic command with system metadata
            result = await self.execute_request(
                variables_code,
                session_id,
                silent=False,
                execution_metadata={"operated_by": "system"},
            )

            # Parse %whos output from the result
            variables = {}
            for output in result.get("outputs", []):
                if (
                    output.get("output_type") == "stream"
                    and output.get("name") == "stdout"
                ):
                    whos_output = output.get("text", "")

                    # Parse standard %whos output format
                    lines = whos_output.strip().split("\n")

                    # Skip header lines and find data
                    for line in lines:
                        line = line.strip()
                        if (
                            line
                            and not line.startswith("Variable")
                            and not line.startswith("---")
                            and not line.startswith("Interactive")
                        ):
                            parts = line.split(None, 3)  # Split into max 4 parts
                            if len(parts) >= 3:
                                name = parts[0]
                                var_type = parts[1]
                                size = parts[2] if len(parts) > 2 else "-"
                                value = parts[3] if len(parts) > 3 else ""

                                variables[name] = {
                                    "type": var_type,
                                    "size": size,
                                    "value": value,
                                }

            return {
                "success": True,
                "variables": variables,
                "session_id": session_id,
                "method": "ipython_whos",
            }

        except Exception as e:
            logger.error(f"Failed to get variables for session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def inspect_variable(
        self, session_id: str, variable_name: str, detail_level: int = 0
    ) -> dict:
        """Inspect specific variable. detail_level: 0=basic info, 1=detailed info"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        try:
            # Use standard inspect_request to get variable information
            # This is the proper Jupyter protocol way to inspect objects
            result = await self.inspect_request(
                code=variable_name,
                cursor_pos=len(variable_name),  # Position at end of variable name
                session_id=session_id,
                detail_level=detail_level,
            )

            if result["success"]:
                inspect_content = result["content"]

                # Check if object was found
                if inspect_content.get("found", False):
                    return {
                        "success": True,
                        "session_id": session_id,
                        "variable_name": variable_name,
                        "found": True,
                        "data": inspect_content.get("data", {}),
                        "metadata": inspect_content.get("metadata", {}),
                        "method": "inspect_request",
                    }
                else:
                    return {
                        "success": True,
                        "session_id": session_id,
                        "variable_name": variable_name,
                        "found": False,
                        "error": f"Variable '{variable_name}' not found",
                        "method": "inspect_request",
                    }
            else:
                return {
                    "success": False,
                    "error": f"Inspect request failed: {result.get('error', 'Unknown error')}",
                }

        except Exception as e:
            logger.error(
                f"Failed to inspect variable '{variable_name}' in session {session_id}: {e}"
            )
            return {"success": False, "error": str(e)}

    @tool
    async def get_variable_names(
        self, session_id: str, pattern: Optional[str] = None
    ) -> dict:
        """Get variable names using %who magic command. Optional pattern to filter by type."""
        if session_id not in self.sessions:
            return {"success": False, "error": f"Session not found: {session_id}"}

        # Use standard IPython %who magic command - the only standard way
        # This matches VS Code notebook and JupyterLab behavior exactly
        if pattern:
            who_code = f"%who {pattern}"
        else:
            who_code = "%who"

        try:
            # Execute using standard magic command with system metadata
            result = await self.execute_request(
                who_code,
                session_id,
                silent=False,
                execution_metadata={"operated_by": "system"},
            )

            # Parse %who output from the result
            names = []
            for output in result.get("outputs", []):
                if (
                    output.get("output_type") == "stream"
                    and output.get("name") == "stdout"
                ):
                    who_output = output.get("text", "")

                    # %who command returns variable names separated by spaces/newlines
                    if who_output.strip():
                        # Split by whitespace and filter out empty strings
                        names = [
                            name.strip() for name in who_output.split() if name.strip()
                        ]

            return {
                "success": True,
                "names": names,
                "session_id": session_id,
                "pattern": pattern,
                "method": "ipython_who",
            }

        except Exception as e:
            logger.error(f"Failed to get variable names for session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    async def _setup_iopub_monitoring(
        self, session_id: str, km: AsyncKernelManager, kc: AsyncKernelClient
    ):
        """Setup IOPub monitoring for a session - unified or individual"""

        def setup_individual_listener():
            """Setup individual IOPub listener for a session"""
            self.iopub_tasks[session_id] = asyncio.create_task(
                self._monitor_iopub(session_id, kc)
            )
            logger.info(f"Started individual IOPub monitoring for session {session_id}")

        if self.use_unified_listener and self.unified_listener:
            success = await self._setup_unified_listener(session_id, km)
            if not success:
                logger.warning(
                    f"Unified listener setup failed for {session_id}, falling back to individual monitoring"
                )
                setup_individual_listener()
        else:
            setup_individual_listener()

    async def _setup_unified_listener(
        self, session_id: str, km: AsyncKernelManager
    ) -> bool:
        """Setup unified listener for a session"""
        if not self.unified_listener:
            logger.warning(f"Unified listener not available for session {session_id}")
            return False

        try:
            connection_info = km.get_connection_info()
            if not connection_info or "iopub_port" not in connection_info:
                logger.warning(f"No valid connection info for session {session_id}")
                return False

            formatted_connection_info = {
                "ip": connection_info.get("ip", "127.0.0.1"),
                "iopub_port": connection_info.get("iopub_port"),
            }

            success = await self.unified_listener.add_kernel(
                session_id,
                formatted_connection_info,
                self._handle_iopub_message,
            )

            if success:
                logger.info(f"Added session {session_id} to unified listener")
            return success

        except Exception as e:
            logger.warning(f"Failed to setup unified listener for {session_id}: {e}")
            return False

    async def _monitor_iopub(self, session_id: str, client: AsyncKernelClient):
        """Monitor IOPub messages and forward to event bus - consistent with unified listener"""
        logger.info(f"Starting IOPub monitoring for session {session_id}")

        while session_id in self.clients:
            try:
                # Get IOPub message - use None timeout for true event-driven behavior!
                msg = await client.get_iopub_msg(timeout=None)

                # Convert to JupyterMessage format (consistent with unified listener)
                jupyter_msg = JupyterMessage(
                    msg_type=msg["header"]["msg_type"],
                    content=msg["content"],
                    header=msg["header"],
                    parent_header=msg.get("parent_header", {}),
                    metadata=msg.get("metadata", {}),
                )

                # Use the same message handler as unified listener for consistency
                await self._handle_iopub_message(session_id, jupyter_msg)

                # Log interesting message types
                msg_type = msg["header"]["msg_type"]
                if msg_type in ["display_data", "execute_result", "error"]:
                    logger.debug(f"IOPub {msg_type} message for session {session_id}")

            except Exception as e:
                logger.error(f"IOPub monitoring error for session {session_id}: {e}")
                await asyncio.sleep(0.1)  # Brief pause before retrying
                continue  # Don't break, keep monitoring

        logger.info(f"Stopped IOPub monitoring for session {session_id}")

    async def subscribe_iopub(
        self, session_id: str, client_id: str, callback=None
    ) -> dict:
        _ = session_id, client_id, callback  # Mark as intentionally unused
        raise NotImplementedError("Backend IOPub subscription not supported")

    async def unsubscribe_iopub(self, subscription_id: str) -> dict:
        _ = subscription_id  # Mark as intentionally unused
        raise NotImplementedError("Backend IOPub unsubscription not supported")

    def _generate_outputs_from_iopub(
        self, iopub_messages: List[dict], execution_count: Optional[int] = None
    ) -> List[dict]:
        """Generate standard notebook outputs from IOPub messages with stream coalescing

        Implements nbclient-style stream merging and terminal control character processing:
        1. Merges consecutive stream outputs with the same name (stdout/stderr)
        2. Processes carriage return (\r) and backspace (\b) characters

        This ensures progress bars and terminal animations display correctly.
        """
        _ = execution_count  # Mark as intentionally unused (for potential future use)
        outputs = []
        streams = {}  # stream_name -> output_dict (for merging)

        # Phase 1: Convert IOPub messages to outputs and merge streams
        for msg in iopub_messages:
            msg_type = msg.get("header", {}).get("msg_type", "")

            # Only process output message types, skip status messages
            if msg_type in ["stream", "execute_result", "display_data", "error"]:
                try:
                    # Use official nbformat API to create output from IOPub message
                    output_node = nbformat.v4.output_from_msg(msg)

                    # Convert NotebookNode to dict
                    output_dict = dict(output_node)

                    if output_dict["output_type"] == "stream":
                        # Merge streams with the same name (nbclient-style)
                        stream_name = output_dict["name"]
                        if stream_name in streams:
                            # Append new text to existing stream
                            streams[stream_name]["text"] += output_dict["text"]
                        else:
                            # New stream: add to outputs and track for merging
                            output_dict["id"] = f"{msg_type}_{len(outputs)}"
                            outputs.append(output_dict)
                            streams[stream_name] = output_dict
                    else:
                        # Non-stream outputs: add directly
                        output_dict["id"] = f"{msg_type}_{len(outputs)}"
                        outputs.append(output_dict)

                except ValueError as e:
                    # If nbformat.output_from_msg fails, log the error and skip
                    logger.warning(
                        f"Failed to create output from IOPub message {msg_type}: {e}"
                    )
                    continue
                except Exception as e:
                    # Fallback for any other errors
                    logger.error(
                        f"Unexpected error processing IOPub message {msg_type}: {e}"
                    )
                    continue

        # Phase 2: Process terminal control characters in merged streams
        # Apply nbclient's exact approach: iterate until no more changes
        for stream in streams.values():
            original_length = len(stream["text"])

            # Process \r and \b characters (exact nbclient algorithm)
            old = stream["text"]
            while len(stream["text"]) < len(old):
                old = stream["text"]
                # Cancel out anything-but-newline followed by backspace
                stream["text"] = _RGX_BACKSPACE.sub("", stream["text"])
            # Replace all carriage returns not followed by newline (OUTSIDE loop - applied once)
            stream["text"] = _RGX_CARRIAGERETURN.sub("", stream["text"])

            if original_length != len(stream["text"]):
                logger.info(
                    f"Processed stream '{stream['name']}': {original_length} → {len(stream['text'])} chars "
                    f"(removed {original_length - len(stream['text'])} chars from \\r/\\b)"
                )

        logger.debug(
            f"Generated {len(outputs)} outputs ({len(streams)} merged streams) from {len(iopub_messages)} IOPub messages"
        )

        return outputs

    async def cleanup(self):
        """Cleanup all resources"""
        # Cancel all IOPub monitoring tasks
        for task in self.iopub_tasks.values():
            task.cancel()

        # Cleanup unified listener if used
        if self.use_unified_listener and self.unified_listener:
            await self.unified_listener.cleanup()

        # Cleanup event bus if it exists and has cleanup method
        if self.event_bus:
            if hasattr(self.event_bus, "cleanup") and callable(
                getattr(self.event_bus, "cleanup")
            ):
                cleanup_method = getattr(self.event_bus, "cleanup")
                await cleanup_method()
            else:
                logger.debug("Event bus doesn't have cleanup method, skipping")

        # Clear message metadata mappings
        self.msg_metadata_mapping.clear()

        # Shutdown all sessions
        for session_id in list(self.sessions.keys()):
            # Clear Jedi contexts if completion service exists
            if hasattr(self, "completion_service"):
                self.completion_service.clear_session_context(session_id)
            await self.shutdown_session(session_id)

        logger.info("JupyterKernelToolSet cleanup complete")


# Export
__all__ = ["JupyterKernelToolSet"]
