from typing import Callable
import uuid
import asyncio

from pantheon.team import Team
from pantheon.internal.memory import Memory
from pantheon.utils.misc import run_func
from pantheon.utils.log import logger


class SteerQueue:
    """A queue of user messages that arrive while the agent is busy.

    The agent loop (`Agent._run_stream`) drains this at turn boundaries so the
    user can steer / send new requests mid-run without waiting for the agent to
    finish. Single-event-loop usage only, so no locking is needed.
    """

    def __init__(self):
        self._messages: list[dict] = []

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
        return msgs

    def peek(self) -> list[dict]:
        """Return a shallow copy of pending messages without clearing them."""
        return list(self._messages)

    def remove(self, message_id: str) -> bool:
        """Remove a pending message by id. Returns True if it was found."""
        before = len(self._messages)
        self._messages = [m for m in self._messages if m.get("id") != message_id]
        return len(self._messages) < before

    def update(self, message_id: str, text: str) -> bool:
        """Replace the text of a pending message by id (used for inline edit of
        a queued message). Returns True if it was found."""
        found = False
        for m in self._messages:
            if m.get("id") == message_id:
                m["content"] = text
                m["_llm_content"] = text
                found = True
        return found

    def __len__(self) -> int:
        return len(self._messages)


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
    ):
        self.id = str(uuid.uuid4())
        self.team = team  # team/team_getter
        self.memory = memory
        self.message = message
        self.context_variables = context_variables or {}
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
        # Suggestions now handled in room.py

    def inject_user_messages(self, message: list[dict]):
        """Queue user message(s) to be picked up mid-run by the agent loop."""
        self.steer_queue.put(message)

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
            team: Team = self.team
            if not isinstance(self.team, Team):
                team = await run_func(self.team)

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
