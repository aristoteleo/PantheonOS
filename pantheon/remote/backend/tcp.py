"""
Local TCP Remote Backend
========================
A transport-local alternative to the NATS backend for ChatRoom<->Endpoint RPC.
Implements the same RemoteBackend / RemoteService / RemoteWorker interface but
routes over localhost TCP instead of a NATS broker, so multiple endpoint
processes can run in one sandbox without crossing the (public, central) NATS.

Wire protocol
-------------
- Framing:   4-byte big-endian length prefix + cloudpickle payload.
- Request:   {"method": str, "parameters": dict, "correlation_id": str}
- Response:  {"correlation_id": str, "result": Any}  or
             {"correlation_id": str, "error": str}

Concurrency / multiplexing
--------------------------
A single persistent connection carries many in-flight requests. The client tags
each request with a correlation_id, keeps a {cid: Future} map, and a background
reader task dispatches each response to its Future. The worker handles every
request in its own task and writes the response (tagged with the same cid) under
a per-connection write lock, so concurrent invokes never cross-talk or block one
another.

Service discovery
-----------------
File registry at <registry_dir>/<service_id>.json holding {host, port, ...}.
The worker writes it on run(); the client reads it on connect(). Default dir is
$PANTHEON_TCP_REGISTRY or ~/.pantheon/tcp-registry (shared within a sandbox).

Streaming
---------
get_or_create_stream is intentionally unimplemented: ChatRoom<->Endpoint is pure
request/reply. Chat-token and notebook streaming flow ChatRoom->frontend over
NATS, not this transport.
"""
import asyncio
import json
import os
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cloudpickle
from funcdesc import parse_func

from pantheon.utils.log import logger
from pantheon.utils.misc import run_func, generate_service_id
from .base import (
    RemoteBackend,
    RemoteService,
    RemoteWorker,
    ServiceInfo,
    StreamType,
    StreamChannel,
)

_LEN = struct.Struct(">I")


def _default_registry_dir() -> str:
    env = os.getenv("PANTHEON_TCP_REGISTRY")
    if env:
        return env
    base = os.getenv("HOME") or os.getenv("USERPROFILE") or "/tmp"
    return str(Path(base) / ".pantheon" / "tcp-registry")


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    hdr = await reader.readexactly(4)
    (n,) = _LEN.unpack(hdr)
    return await reader.readexactly(n)


async def _write_frame(writer: asyncio.StreamWriter, payload: bytes, lock: asyncio.Lock):
    # Lock guards a full (length-prefix + payload) write so concurrent responders
    # on one connection never interleave their bytes.
    async with lock:
        writer.write(_LEN.pack(len(payload)) + payload)
        await writer.drain()


# ----------------------------- Worker (server side) -----------------------------
class TCPRemoteWorker(RemoteWorker):
    def __init__(self, backend: "TCPBackend", service_name: str, description: str = "", **kwargs):
        self._backend = backend
        self._service_name = service_name
        self._description = description
        self._host = kwargs.get("host", "127.0.0.1")
        self._port = int(kwargs.get("port", 0))  # 0 => OS assigns a free port
        id_hash = kwargs.get("id_hash") or f"{service_name}_{uuid.uuid4().hex[:8]}"
        self._service_id = generate_service_id(str(id_hash))
        self._registry_dir = Path(backend.registry_dir)
        self._functions: Dict[str, Callable] = {}
        self._running = False
        self._server: Optional[asyncio.AbstractServer] = None
        self._activity_callback: Optional[Callable[[], dict]] = None
        self._on_ready: Optional[asyncio.Event] = None
        # Parity with NATS worker: auto-register ping for connection checks.
        self.register(self._ping)

    def set_activity_callback(self, callback: Callable[[], dict]):
        self._activity_callback = callback

    async def _ping(self) -> dict:
        from pantheon import __version__
        result = {"status": "ok", "service_id": self._service_id, "version": __version__}
        if self._activity_callback:
            try:
                result.update(self._activity_callback())
            except Exception:
                pass
        return result

    def register(self, func: Callable, **kwargs):
        self._functions[func.__name__] = func

    async def run(self):
        self._registry_dir.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        addr = self._server.sockets[0].getsockname()
        self._host, self._port = addr[0], addr[1]
        self._write_registry()
        self._running = True
        logger.info(
            f"[TCPWorker] {self._service_name} ({self._service_id[:12]}…) "
            f"listening on {self._host}:{self._port}"
        )
        if self._on_ready is not None:
            self._on_ready.set()
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            pass

    def _write_registry(self):
        data = {
            "service_id": self._service_id,
            "service_name": self._service_name,
            "description": self._description,
            "host": self._host,
            "port": self._port,
            "pid": os.getpid(),
            "registered_at": time.time(),
        }
        f = self._registry_dir / f"{self._service_id}.json"
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, f)  # atomic publish

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        wlock = asyncio.Lock()
        try:
            while True:
                data = await _read_frame(reader)
                # Concurrent dispatch: each request runs in its own task; the
                # response carries its correlation_id so replies may return out
                # of order without the client confusing them.
                asyncio.create_task(self._process_and_respond(data, writer, wlock))
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:
            logger.error(f"[TCPWorker] client handler error: {e}")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _process_and_respond(self, data: bytes, writer: asyncio.StreamWriter, wlock: asyncio.Lock):
        cid = None
        method = None
        try:
            msg = cloudpickle.loads(data)
            cid = msg.get("correlation_id")
            method = msg.get("method")
            params = msg.get("parameters") or {}
            if method not in self._functions:
                await self._respond(writer, wlock, {
                    "correlation_id": cid,
                    "error": f"Method {method} not found on service {self._service_id}",
                })
                return
            result = await run_func(self._functions[method], **params)
            await self._respond(writer, wlock, {"correlation_id": cid, "result": result})
        except Exception as e:
            import traceback
            logger.error(
                f"[TCPWorker] error processing {method}: {e}\n{traceback.format_exc()}"
            )
            await self._respond(writer, wlock, {"correlation_id": cid, "error": str(e)})

    async def _respond(self, writer, wlock, obj):
        try:
            await _write_frame(writer, cloudpickle.dumps(obj), wlock)
        except Exception as e:
            logger.error(f"[TCPWorker] failed to send response: {e}")

    async def stop(self):
        self._running = False
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
        f = self._registry_dir / f"{self._service_id}.json"
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass

    def get_service_info(self) -> ServiceInfo:
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

    @property
    def service_id(self) -> str:
        return self._service_id

    @property
    def service_name(self) -> str:
        return self._service_name

    @property
    def servers(self) -> List[str]:
        return [f"tcp://{self._host}:{self._port}"]

    @property
    def functions(self) -> Dict[str, tuple]:
        return {
            name: (func, getattr(func, "__doc__", ""))
            for name, func in self._functions.items()
        }


# ----------------------------- Service (client side) -----------------------------
class TCPService(RemoteService):
    def __init__(
        self,
        service_id: str,
        host: str,
        port: int,
        registry_dir: str,
        timeout: float | None = None,
        **kwargs,
    ):
        self.service_id = service_id
        self._host = host
        self._port = port
        self._registry_dir = Path(registry_dir)
        if timeout is not None:
            self.timeout = timeout
        else:
            try:
                from pantheon.settings import get_settings
                self.timeout = get_settings().tool_timeout
            except Exception:
                self.timeout = 3600
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._wlock = asyncio.Lock()
        self._conn_lock = asyncio.Lock()
        self._pending: Dict[str, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._service_info = ServiceInfo(service_id, "", "", {})

    async def _ensure_connected(self):
        if self._writer is not None and not self._writer.is_closing():
            return
        async with self._conn_lock:
            if self._writer is not None and not self._writer.is_closing():
                return
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
            self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            while True:
                data = await _read_frame(self._reader)
                resp = cloudpickle.loads(data)
                cid = resp.get("correlation_id")
                fut = self._pending.pop(cid, None)
                if fut is not None and not fut.done():
                    fut.set_result(resp)
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.error(f"[TCPService] read loop error: {e}")
        finally:
            exc = ConnectionError(f"TCP connection to service {self.service_id} closed")
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(exc)
            self._pending.clear()
            self._writer = None

    async def invoke(self, method: str, parameters: Optional[Dict[str, Any]] = None) -> Any:
        if parameters is None:
            parameters = {}
        await self._ensure_connected()
        cid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[cid] = fut
        payload = cloudpickle.dumps(
            {"method": method, "parameters": parameters, "correlation_id": cid}
        )
        try:
            await _write_frame(self._writer, payload, self._wlock)
        except Exception as e:
            self._pending.pop(cid, None)
            raise ConnectionError(f"Failed to send request to {self.service_id}: {e}")
        try:
            resp = await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cid, None)
            raise Exception(f"Timeout calling {method} on {self.service_id}")
        if resp.get("error"):
            raise Exception(resp["error"])
        return resp.get("result")

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._writer = None

    @property
    def service_info(self) -> ServiceInfo:
        return self._service_info

    async def fetch_service_info(self) -> ServiceInfo:
        f = self._registry_dir / f"{self.service_id}.json"
        if not f.exists():
            raise RuntimeError(
                f"Service '{self.service_id}' not found in TCP registry {self._registry_dir}"
            )
        d = json.loads(f.read_text())
        self._service_info = ServiceInfo(
            d["service_id"], d.get("service_name", ""), d.get("description", ""), {}
        )
        return self._service_info


# ----------------------------- Backend (factory) -----------------------------
class TCPBackend(RemoteBackend):
    def __init__(self, registry_dir: str | None = None, **kwargs):
        self.registry_dir = registry_dir or _default_registry_dir()
        Path(self.registry_dir).mkdir(parents=True, exist_ok=True)

    async def connect(self, service_id: str, **kwargs) -> RemoteService:
        f = Path(self.registry_dir) / f"{service_id}.json"
        if not f.exists():
            raise ConnectionError(
                f"Service '{service_id}' not found in TCP registry {self.registry_dir}"
            )
        d = json.loads(f.read_text())
        # Drop transport keys we set ourselves so they don't clash with kwargs.
        for k in ("server_urls",):
            kwargs.pop(k, None)
        return TCPService(service_id, d["host"], int(d["port"]), self.registry_dir, **kwargs)

    def create_worker(self, service_name: str, **kwargs) -> RemoteWorker:
        return TCPRemoteWorker(self, service_name, **kwargs)

    @property
    def servers(self) -> List[str]:
        return [f"tcp-local://{self.registry_dir}"]

    async def get_or_create_stream(
        self, stream_id: str, stream_type: StreamType = StreamType.CUSTOM, **kwargs
    ) -> StreamChannel:
        raise NotImplementedError(
            "TCP backend does not implement streaming. ChatRoom<->Endpoint is pure "
            "request/reply; chat/notebook streaming flows to the frontend over NATS."
        )
