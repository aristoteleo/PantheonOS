from typing import Callable
import uuid
import asyncio

from ..team import PantheonTeam
from ..memory import Memory
from ..utils.misc import run_func
from ..utils.log import logger


class Thread:
    """A thread is a single chat in a chatroom.

    Args:
        team: The team to use for the thread.
        memory: The memory to use for the thread.
        message: The message to send to the thread.
        run_hook_timeout: The timeout for the hook.
        hook_retry_times: The number of times to retry the hook.
    """
    def __init__(
            self,
            team: PantheonTeam,
            memory: Memory,
            message: list[dict],
            run_hook_timeout: float = 1.0,
            hook_retry_times: int = 5,
            ):
        self.id = str(uuid.uuid4())
        self.team = team
        self.memory = memory
        self.message = message
        self._process_chunk_hooks: list[Callable] = []
        self._process_step_message_hooks: list[Callable] = []
        self.response = None
        self.run_hook_timeout = run_hook_timeout
        self.hook_retry_times = hook_retry_times
        self._stop_flag = False

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
                            run_func(hook, chunk),
                            timeout=self.run_hook_timeout
                        )
                        return res
                    except Exception as e:
                        logger.debug(f"Failed run hook {hook.__name__} for chunk {chunk}, retry {_ + 1} of {self.hook_retry_times}")
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
                        run_func(hook, step_message),
                        timeout=self.run_hook_timeout
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
            if len(self.memory.get_messages()) == 0:
                # summary to get new name using LLM
                prompt = "Please summarize the question to get a name for the chat: \n"
                prompt += str(self.message)
                prompt += "\n\nPlease directly return the name, no other text or explanation."
                new_name = await self.team.run(prompt, use_memory=False, update_memory=False)
                self.memory.name = new_name.content

            resp = await self.team.run(
                self.message,
                memory=self.memory,
                process_chunk=self.process_chunk,
                process_step_message=self.process_step_message,
                check_stop=self._check_stop,
            )
            self.response = {"success": True, "response": resp.content, "chat_id": self.memory.id}
        except Exception as e:
            logger.error(f"Error chatting: {e}")
            import traceback
            traceback.print_exc()
            self.response = {"success": False, "message": str(e), "chat_id": self.memory.id}

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
        
