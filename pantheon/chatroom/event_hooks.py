"""Transport-independent Agent event shaping, shared by local and remote hosts."""


import re

_APP = re.compile(r'"app"\s*:\s*"([^"]+)"')
_TOOL = re.compile(r'"tool"\s*:\s*"([^"]+)"')
_ARGS = re.compile(r'"args"\s*:\s*')


def _app_tool_delta(state: dict, delta: str) -> str:
    """The part of a streamed call_app_tool's arguments that belongs to the
    named tool's own arguments, once app, tool and "args" have arrived."""
    state["buffer"] = state.get("buffer", "") + delta
    buffer = state["buffer"]
    if "display" not in state:
        app, tool, args = _APP.search(buffer), _TOOL.search(buffer), _ARGS.search(buffer)
        if not (app and tool and args):
            return ""
        name = tool.group(1)
        state["display"] = name if app.group(1) == "agent" else f"{app.group(1)}__{name}"
        state["sent"] = args.end()
    out = buffer[state["sent"]:]
    state["sent"] = len(buffer)
    return out


class ChatEventHooks:
    """Subclasses supply async publish(chat_id, message_type, data)."""

    def create_hooks(self, chat_id: str):
        """Create transport-independent hooks for chat()

        Returns:
            tuple: (chunk_hook, step_hook)
        """
        # Track current tool call state for argument streaming
        _tool_call_state = {}

        async def chunk_hook(chunk: dict):
            # Detect tool_calls argument deltas in the chunk
            tool_calls = chunk.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    name = fn.get("name")
                    args_delta = fn.get("arguments", "")
                    if name:
                        _tool_call_state.clear()
                        _tool_call_state["name"] = name
                    if args_delta and _tool_call_state.get("name") == "call_app_tool":
                        # Stream as the tool it names (as history records it):
                        # the UI picks its card and live preview by that name.
                        args_delta = _app_tool_delta(_tool_call_state, args_delta)
                        if not args_delta:
                            continue
                        await self.publish(
                            chat_id,
                            "tool_delta",
                            {
                                "type": "tool_delta",
                                "tool_name": _tool_call_state["display"],
                                "delta": args_delta,
                                "message_id": chunk.get("message_id"),
                                "execution_context_id": chunk.get("execution_context_id"),
                            },
                        )
                    elif args_delta and _tool_call_state.get("name"):
                        await self.publish(
                            chat_id,
                            "tool_delta",
                            {
                                "type": "tool_delta",
                                "tool_name": _tool_call_state["name"],
                                "delta": args_delta,
                                "message_id": chunk.get("message_id"),
                                "execution_context_id": chunk.get("execution_context_id"),
                            },
                        )
                return

            # Check for begin/stop signals
            if chunk.get("begin") or chunk.get("stop"):
                _tool_call_state.clear()
                # Begin exposes the wait for each model round, before any text
                # or reasoning arrives. Stop finalizes tool argument streaming.
                await self.publish(
                    chat_id,
                    "chunk",
                    {"type": "chunk", "chunk": chunk},
                )
                return

            # Regular text chunk — clear tool state and publish
            content = chunk.get("content")
            if content:
                _tool_call_state.clear()

            await self.publish(chat_id, "chunk", {"type": "chunk", "chunk": chunk})

        async def step_hook(step_message: dict):
            # Filter out user messages to avoid duplication on frontend — EXCEPT
            # steer-drained messages (message queue feature), whose echo the
            # frontend needs to clear the "Queued" badge, and a sub-agent's
            # (its task, which the Team view shows while it works). Dedup-by-id
            # on the frontend prevents any duplicate display.
            if (step_message.get("role") == "user" and not step_message.get("_steer_drained")
                    and not step_message.get("execution_context_id")):
                return
            # A step message arriving means any tool streaming is done
            _tool_call_state.clear()
            await self.publish(
                chat_id,
                "step",
                {"type": "step_message", "step_message": step_message},
            )

        return chunk_hook, step_hook

    async def publish_chat_finished(self, chat_id: str):
        """Publish chat finished message"""
        await self.publish(chat_id, "chat_finished", {"type": "chat_finished"})
