from typing import Callable
import uuid
import asyncio

from pantheon.team import Team
from pantheon.internal.memory import Memory
from pantheon.utils.misc import run_func
from pantheon.utils.log import logger


#: Where a conversation keeps what the user said to its sub-agents directly
#: until the lead's next turn.
SUB_AGENT_NOTES = "sub_agent_notes"


def _text(message: list[dict]) -> str:
    parts = []
    for msg in message if isinstance(message, list) else [message]:
        content = msg.get("content") if isinstance(msg, dict) else msg
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        parts.append(str(content or ""))
    return "\n".join(p for p in parts if p).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def add_sub_agent_note(memory: Memory, agent: str, context: str, message: list[dict], answer,
                       started: bool = False) -> None:
    """Remember that the user talked to a sub-agent directly (or `started` a
    run of one), for the lead."""
    notes = list((memory.extra_data or {}).get(SUB_AGENT_NOTES) or [])
    notes.append({"agent": agent, "context": context, "asked": _clip(_text(message), 500),
                  "answered": _clip(str(answer or ""), 1500), "started": started})
    memory.set_metadata(SUB_AGENT_NOTES, notes[-10:])


def take_sub_agent_notes(memory: Memory) -> str | None:
    """The lead's note of what the user said to sub-agents since its last turn
    (each sub-agent's own messages are not in the lead's view)."""
    notes = (memory.extra_data or {}).get(SUB_AGENT_NOTES)
    if not notes:
        return None
    memory.set_metadata(SUB_AGENT_NOTES, [])
    lines = []
    for note in notes:
        answered = note.get("answered") or "(it stopped without an answer)"
        agent = note.get("agent") or "A sub-agent"
        did = "The user started" if note.get("started") else "The user asked"
        lines.append(f"- {did} {agent} (run {note.get('context')}): {note.get('asked')!r}\n"
                     f"  It answered: {answered}")
    return ("<sub_agent_updates>\nSince your last turn the user talked to sub-agents directly:\n"
            + "\n".join(lines) + "\n</sub_agent_updates>")


class SteerQueue:
    """A queue of user messages that arrive while the agent is busy.

    The agent loop (`Agent._run_stream`) drains this at turn boundaries so the
    user can steer / send new requests mid-run without waiting for the agent to
    finish. Single-event-loop usage only, so no locking is needed.

    A running sub-agent has a queue of its own (open_run): the messages the
    user sends to that sub-agent, and whether the user stopped it. A sub-agent
    the lead waits on (call_agent) also reads the conversation's messages, as
    every sub-agent did before one could be addressed.
    """

    def __init__(self, conversation: "SteerQueue | None" = None):
        self._messages: list[dict] = []
        self._conversation = conversation
        self._runs: dict[str, SteerQueue] = {}
        # Set by the user (stop_sub_agent) on a sub-agent's queue.
        self.stopped = False
        # Called with (context, messages) for messages a sub-agent run ended
        # without reading; the thread continues the sub-agent with them.
        self.on_unread: Callable[[str, list[dict]], None] | None = None

    def put(self, messages: list[dict]):
        """Enqueue one or more user messages."""
        if isinstance(messages, list):
            self._messages.extend(messages)
        else:
            self._messages.append(messages)

    def drain(self) -> list[dict]:
        """Atomically take and clear all pending messages."""
        msgs = self._messages
        self._messages = []
        if self._conversation is not None:
            msgs = msgs + self._conversation.drain()
        return msgs

    def peek(self) -> list[dict]:
        """Return a shallow copy of pending messages without clearing them,
        those sent to running sub-agents included."""
        pending = list(self._messages)
        for run in self._runs.values():
            pending.extend(run._messages)
        return pending

    def _queues(self):
        yield self
        yield from self._runs.values()

    def remove(self, message_id: str) -> bool:
        """Remove a pending message by id. Returns True if it was found."""
        found = False
        for queue in self._queues():
            before = len(queue._messages)
            queue._messages = [m for m in queue._messages if m.get("id") != message_id]
            found = found or len(queue._messages) < before
        return found

    def update(self, message_id: str, text: str) -> bool:
        """Replace the text of a pending message by id (used for inline edit of
        a queued message). Returns True if it was found."""
        found = False
        for queue in self._queues():
            for m in queue._messages:
                if m.get("id") == message_id:
                    m["content"] = text
                    m["_llm_content"] = text
                    found = True
        return found

    def open_run(self, context: str, *, shared: bool = False) -> "SteerQueue":
        """The queue of a sub-agent run starting now (its execution context).
        shared: it also reads the messages sent to the conversation."""
        queue = self._runs[context] = SteerQueue(self if shared else None)
        return queue

    def run_queue(self, context: str) -> "SteerQueue | None":
        """The queue of a running sub-agent, or None when it is not running."""
        return self._runs.get(context)

    def close_run(self, context: str) -> None:
        """A sub-agent run ended. Messages it did not read continue it, unless
        the user stopped it (as Stop drops the conversation's queue)."""
        queue = self._runs.pop(context, None)
        if queue is None or not queue._messages or queue.stopped:
            return
        if self.on_unread is not None:
            self.on_unread(context, queue._messages)

    def __len__(self) -> int:
        conversation = len(self._conversation) if self._conversation is not None else 0
        return len(self._messages) + conversation


class Thread:
    """A thread is a single chat in a chatroom.

    Args:
        team: The team/team_getter to use for the thread.
        memory: The memory to use for the thread.
        message: The message to send to the thread.
        run_hook_timeout: The timeout for the hook.
        hook_retry_times: The number of times to retry the hook.
    """

    def __init__(
        self,
        team,  # team/team_getter
        memory: Memory,
        message: list[dict],
        context_variables: dict | None = None,
        run_hook_timeout: float = 1.0,
        hook_retry_times: int = 5,
        sub_agent: str | None = None,
        sub_agent_profile: str | None = None,
        agent_for: Callable | None = None,
    ):
        self.id = str(uuid.uuid4())
        self.team = team  # team/team_getter
        self.memory = memory
        self.message = message
        self.context_variables = context_variables or {}
        # The execution context of a sub-agent this thread continues with the
        # message, instead of running the lead; with a profile, a new run of
        # that profile the user starts.
        self.sub_agent = sub_agent
        self.sub_agent_profile = sub_agent_profile
        # async (context) -> the Agent a run of an agent built elsewhere (a
        # profile file, a team) continues with; None for the team's own.
        self.agent_for = agent_for
        # Sub-agents the user messaged while this thread ran; it ends with them.
        self._side_runs: set[asyncio.Task] = set()
        self._process_chunk_hooks: list[Callable] = []
        self._process_step_message_hooks: list[Callable] = []
        self.response = None
        self.run_hook_timeout = run_hook_timeout
        self.hook_retry_times = hook_retry_times
        self._stop_flag = False
        # Set by room.chat() when this thread's run AND all its post-run memory
        # writes are done. revert_to_message awaits it so a revert can't race the
        # run and delete before the run's (late) writes land.
        self._done = asyncio.Event()
        # Queue for user messages that arrive while this thread is running.
        # The agent loop drains it at turn boundaries (message queue feature);
        # leftovers after the run are flushed by room.chat().
        self.steer_queue = SteerQueue()
        self.steer_queue.on_unread = self._continue_beside
        # Suggestions now handled in room.py

    def inject_user_messages(self, message: list[dict]):
        """Queue user message(s) to be picked up mid-run by the agent loop."""
        self.steer_queue.put(message)

    def message_sub_agent(self, context: str, message: list[dict], profile: str | None = None) -> dict:
        """A message the user sent one sub-agent while this thread runs. A
        running sub-agent reads it at its next turn; a finished one continues
        beside the run, and so does a new run of `profile`."""
        queue = self.steer_queue.run_queue(context) if profile is None else None
        if queue is not None:
            queue.put(message)
            return {"success": True, "queued": True}
        if self._stop_flag:
            return {"success": False, "message": "The conversation is stopping"}
        self._continue_beside(context, message, profile)
        return {"success": True}

    def stop_sub_agent(self, context: str) -> bool:
        """Stop one running sub-agent; False when it is not running."""
        queue = self.steer_queue.run_queue(context)
        if queue is None:
            return False
        queue.stopped = True
        return True

    def _continue_beside(self, context: str, message: list[dict], profile: str | None = None,
                         note: bool = True) -> "asyncio.Task | None":
        if self._stop_flag:
            return None
        task = asyncio.get_running_loop().create_task(self._continue(context, message, profile, note))
        self._side_runs.add(task)
        task.add_done_callback(self._side_runs.discard)
        return task

    async def _team(self) -> Team:
        if isinstance(self.team, Team):
            return self.team
        return await run_func(self.team)

    async def _continue(self, context: str, message: list[dict], profile: str | None = None,
                        note: bool = True):
        """Continue a sub-agent with the user's message (or start a run of
        `profile`), then leave the lead a note of it (delivered at the lead's
        next turn, see sub_agent_notes); an agent's own message (a command's
        --resume) needs none."""
        team = await self._team()
        agent = await self.agent_for(context) if self.agent_for is not None and profile is None else None
        queue = self.steer_queue.open_run(context)
        try:
            response = await team.continue_sub_agent(
                context,
                message,
                memory=self.memory,
                profile=profile,
                agent=agent,
                context_variables=dict(self.context_variables),
                process_chunk=self.process_chunk,
                process_step_message=self.process_step_message,
                check_stop=lambda *_: self._stop_flag or queue.stopped,
            )
        finally:
            self.steer_queue.close_run(context)
        if note:
            add_sub_agent_note(self.memory, getattr(response, "agent_name", ""), context,
                               message, getattr(response, "content", ""), started=profile is not None)
        return response

    def add_chunk_hook(self, hook: Callable):
        """Add a chunk hook to the thread.

        Args:
            hook: The hook to add.
        """
        self._process_chunk_hooks.append(hook)

    def add_step_message_hook(self, hook: Callable):
        """Add a step message hook to the thread.

        Args:
            hook: The hook to add.
        """
        self._process_step_message_hooks.append(hook)

    async def process_chunk(self, chunk: dict):
        """Process a chunk of the thread.

        Args:
            chunk: The chunk to process.
        """
        chunk["chat_id"] = self.memory.id
        _coros = []
        for hook in self._process_chunk_hooks:

            async def _run_hook(hook: Callable, chunk: dict):
                res = None
                error = None
                for _ in range(self.hook_retry_times):
                    try:
                        res = await asyncio.wait_for(
                            run_func(hook, chunk), timeout=self.run_hook_timeout
                        )
                        return res
                    except Exception as e:
                        logger.debug(
                            f"Failed run hook {hook.__name__} for chunk {chunk}, retry {_ + 1} of {self.hook_retry_times}"
                        )
                        error = e
                        continue
                else:
                    logger.error(f"Error running process_chunk hook: {error}")
                    self._process_chunk_hooks.remove(hook)

            _coros.append(_run_hook(hook, chunk))
        await asyncio.gather(*_coros)

    async def process_step_message(self, step_message: dict):
        """Process a step message of the thread.

        Args:
            step_message: The step message to process.
        """
        step_message["chat_id"] = self.memory.id
        _coros = []
        for hook in self._process_step_message_hooks:

            async def _run_hook(hook: Callable, step_message: dict):
                res = None
                try:
                    res = await asyncio.wait_for(
                        run_func(hook, step_message), timeout=self.run_hook_timeout
                    )
                except Exception as e:
                    logger.error(f"Error running process_step_message hook: {str(e)}")
                    self._process_step_message_hooks.remove(hook)
                return res

            _coros.append(_run_hook(hook, step_message))
        await asyncio.gather(*_coros)

    async def run(self):
        """Run the thread.

        Returns:
            The response of the thread.
        """
        try:
            # Ensure team is ready (create custom team if needed)
            team = await self._team()

            if self.sub_agent:
                resp = await self._continue(self.sub_agent, self.message, self.sub_agent_profile)
            else:
                resp = await team.run(
                    self.message,
                    memory=self.memory,
                    context_variables=self.context_variables,
                    process_chunk=self.process_chunk,
                    process_step_message=self.process_step_message,
                    check_stop=self._check_stop,
                )
            self.response = {
                "success": True,
                "response": resp.content,
                "chat_id": self.memory.id,
            }

            # Suggestions are now handled in room.py
        except Exception as e:
            logger.error(f"Error chatting: {e}")
            import traceback

            traceback.print_exc()
            self.response = {
                "success": False,
                "message": str(e),
                "chat_id": self.memory.id,
            }
        except asyncio.CancelledError:
            for task in self._side_runs:
                task.cancel()
            raise
        finally:
            # The run ends with the sub-agents the user messaged during it.
            while self._side_runs:
                results = await asyncio.gather(*list(self._side_runs), return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"A sub-agent the user messaged failed: {result}")

    def _check_stop(self, *args, **kwargs):
        """Check if the thread should be stopped.

        Returns:
            Whether the thread should be stopped.
        """
        return self._stop_flag

    async def stop(self):
        """Stop the thread.

        Returns:
            The response of the thread.
        """
        self._stop_flag = True

    # All suggestion methods moved to room.py
