"""Live widget traffic, independent of the execute_interactive IOPub reader.

Each kernel has a second Jupyter client. Its bounded, cursor-based journal can
be read by multiple windows through the existing authenticated App RPC, including
Fleet nodes. No public kernel ports, extra web server or frontend code execution.
"""
import asyncio
import base64
from collections import deque
from datetime import datetime
import json
from queue import Empty
import uuid
from jupyter_client.session import Session


CHUNK_SIZE = 48 * 1024
MAX_FRAMES = 512
MAX_MESSAGE = 16 * 1024 * 1024
TARGETS = {"jupyter.widget", "jupyter.widget.control"}
OUTPUTS = {"stream", "display_data", "update_display_data", "execute_result", "error", "clear_output", "status"}


def wire_message(msg):
    return {
        "header": msg.get("header", {}),
        "parent_header": msg.get("parent_header", {}),
        "metadata": msg.get("metadata", {}),
        "content": msg.get("content", {}),
        "buffers": [base64.b64encode(bytes(b)).decode("ascii") for b in msg.get("buffers", [])],
    }


class WidgetBridge:
    def __init__(self, client):
        self.client = client
        self.generation = uuid.uuid4().hex
        self.frames = deque(maxlen=MAX_FRAMES)
        self.cursor = 0
        self.changed = asyncio.Event()
        self.closed = False
        self.comms = {}
        self.canvas_targets = {}
        self.pending = {}
        self.tasks = []

    @classmethod
    async def create(cls, manager):
        # manager.client() otherwise reuses its Session (ZMQ identity and HMAC
        # digest history), which makes two clients steal/reject each other's
        # messages. Only the signing key is shared.
        client = manager.client(session=Session(key=manager.session.key,
                                               signature_scheme=manager.session.signature_scheme))
        client.start_channels(stdin=False, hb=False)
        try:
            await client.wait_for_ready(timeout=30)
        except BaseException:
            client.stop_channels()
            raise
        bridge = cls(client)
        bridge.tasks = [asyncio.create_task(bridge._iopub()), asyncio.create_task(bridge._shell())]
        return bridge

    def _record(self, msg):
        msg = self._canvas_message(msg)
        payload = json.dumps(wire_message(msg), default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v))
        # Oversize messages invalidate the cursor instead of silently delivering
        # a corrupt partial state. The viewer re-requests live model state.
        if len(payload) > MAX_MESSAGE:
            self.frames.clear()
            self.cursor += 1
            self.changed.set()
            return
        parts = [payload[i:i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
        for index, part in enumerate(parts):
            self.cursor += 1
            self.frames.append({"seq": self.cursor, "id": msg["header"]["msg_id"],
                                "index": index, "total": len(parts), "data": part})
        self.changed.set()

    def _canvas_message(self, msg):
        """Make ipycanvas 0.14 draw batches independently replayable.

        Its Python manager only sends switchCanvas when the target changes.
        That target is not part of the widget state snapshot, so a new viewer
        after journal eviction otherwise receives drawing commands with no
        current canvas. Prefix the last explicit target, preserving all binary
        drawing buffers. No drawing or Python execution happens in this bridge.
        """
        kind = msg.get("header", {}).get("msg_type")
        content = msg.get("content", {})
        comm_id = content.get("comm_id")
        data = content.get("data", {})
        state = data.get("state", {})
        if kind == "comm_open" and state.get("_model_module") == "ipycanvas" and state.get("_model_name") == "CanvasManagerModel":
            if "0.14" in state.get("_model_module_version", ""):
                self.canvas_targets[comm_id] = None
        elif kind == "comm_close":
            self.canvas_targets.pop(comm_id, None)
        elif kind == "comm_msg" and comm_id in self.canvas_targets and data.get("method") == "custom":
            buffers = msg.get("buffers", [])
            if not buffers or data.get("content", {}).get("dtype") != "uint8":
                return msg
            try:
                commands = json.loads(bytes(buffers[0]))
                batch = commands if isinstance(commands[0], list) else [commands]
                previous = self.canvas_targets[comm_id]
                for command in batch:
                    # switchCanvas is opcode 60 in the bundled 0.14 protocol.
                    if command[0] == 60:
                        self.canvas_targets[comm_id] = command
                if previous is not None:
                    encoded = json.dumps([previous, *batch]).encode()
                    descriptor = {**data["content"], "shape": [len(encoded)]}
                    return {**msg, "content": {**content, "data": {**data, "content": descriptor}},
                            "buffers": [encoded, *buffers[1:]]}
            except (ValueError, TypeError, IndexError, KeyError):
                pass  # Unknown extension payloads remain untouched.
        return msg

    async def _iopub(self):
        while not self.closed:
            try:
                msg = await self.client.get_iopub_msg(timeout=1)
            except Empty:
                continue
            kind = msg["header"]["msg_type"]
            content = msg["content"]
            comm_id = content.get("comm_id")
            if kind == "comm_open" and content.get("target_name") in TARGETS:
                self.comms[comm_id] = content["target_name"]
                self._record(msg)
            elif kind in {"comm_msg", "comm_close"} and comm_id in self.comms:
                self._record(msg)
                if kind == "comm_close":
                    self.comms.pop(comm_id, None)
            elif kind in OUTPUTS and self.comms:
                # Output widgets capture callback output after a cell is idle.
                self._record(msg)

    async def _shell(self):
        while not self.closed:
            try:
                msg = await self.client.get_shell_msg(timeout=1)
            except Empty:
                continue
            future = self.pending.pop(msg.get("parent_header", {}).get("msg_id"), None)
            if future and not future.done():
                future.set_result(msg["content"])

    async def info(self):
        msg_id = self.client.comm_info(target_name="jupyter.widget")
        future = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = future
        try:
            result = await asyncio.wait_for(future, 10)
            comms = result.get("comms", {})
            self.comms.update({key: val["target_name"] for key, val in comms.items()})
            return comms
        finally:
            self.pending.pop(msg_id, None)

    async def poll(self, cursor, generation, wait=15):
        if generation != self.generation:
            return {"success": True, "reset": True, "generation": self.generation, "cursor": self.cursor, "frames": []}
        if cursor == self.cursor and not self.closed:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), min(max(wait, 0), 20))
            except asyncio.TimeoutError:
                pass
        first = self.frames[0]["seq"] if self.frames else self.cursor + 1
        reset = cursor < first - 1 or cursor > self.cursor or self.closed
        frames = [] if reset else [f for f in self.frames if f["seq"] > cursor][:8]
        return {"success": True, "generation": self.generation, "reset": reset,
                "cursor": frames[-1]["seq"] if frames else self.cursor, "frames": frames}

    def send(self, generation, message):
        if self.closed or generation != self.generation:
            raise ValueError("Kernel changed. Reconnect widgets before sending input.")
        kind = message.get("msg_type")
        content = message.get("content", {})
        comm_id = content.get("comm_id")
        if kind not in {"comm_open", "comm_msg", "comm_close"} or not isinstance(comm_id, str) or not comm_id:
            raise ValueError("Only widget comm messages are allowed")
        if kind == "comm_open":
            if content.get("target_name") not in TARGETS:
                raise ValueError("Unsupported widget comm target")
        elif comm_id not in self.comms:
            raise ValueError("Widget comm no longer exists. Re-run its cell.")
        if len(json.dumps(message)) > MAX_MESSAGE:
            raise ValueError("Widget message is too large")
        buffers = [base64.b64decode(b, validate=True) for b in message.get("buffers", [])]
        msg = self.client.session.msg(kind, content=content, metadata=message.get("metadata", {}))
        # Preserve the frontend id so status/output callbacks can be correlated.
        msg_id = message.get("msg_id")
        if not isinstance(msg_id, str) or not 1 <= len(msg_id) <= 128:
            raise ValueError("Invalid widget message id")
        if kind == "comm_open":
            self.comms[comm_id] = content["target_name"]
        msg["header"]["msg_id"] = msg_id
        self.client.session.send(self.client.shell_channel.socket, msg, buffers=buffers)
        return msg_id

    async def close(self):
        self.closed = True
        self.changed.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()
        self.client.stop_channels()
        self.frames.clear()
