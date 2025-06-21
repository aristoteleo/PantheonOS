from typing import Callable
import uuid
import asyncio

from ..team import SwarmCenterTeam
from ..memory import Memory
from ..utils.misc import run_func
from ..utils.log import logger


class Thread:
    def __init__(
            self,
            team: SwarmCenterTeam,
            memory: Memory,
            message: list[dict],
            run_hook_timeout: int = 5,
            ):
        self.id = str(uuid.uuid4())
        self.team = team
        self.memory = memory
        self.message = message
        self._process_chunk_hooks: list[Callable] = []
        self._process_step_message_hooks: list[Callable] = []
        self.response = None
        self.run_hook_timeout = run_hook_timeout

    def add_chunk_hook(self, hook: Callable):
        self._process_chunk_hooks.append(hook)

    def add_step_message_hook(self, hook: Callable):
        self._process_step_message_hooks.append(hook)

    async def process_chunk(self, chunk: dict):
        chunk["chat_id"] = self.memory.id
        _coros = []
        for hook in self._process_chunk_hooks:
            async def _run_hook(hook: Callable, chunk: dict):
                res = None
                try:
                    res = await asyncio.wait_for(
                        run_func(hook, chunk),
                        timeout=self.run_hook_timeout
                    )
                except Exception as e:
                    logger.error(f"Error running process_chunk hook: {str(e)}")
                    self._process_chunk_hooks.remove(hook)
                return res
            _coros.append(_run_hook(hook, chunk))
        await asyncio.gather(*_coros)

    async def process_step_message(self, step_message: dict):
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
            )
            self.response = {"success": True, "response": resp.content, "chat_id": self.memory.id}
        except Exception as e:
            logger.error(f"Error chatting: {e}")
            import traceback
            traceback.print_exc()
            self.response = {"success": False, "message": str(e), "chat_id": self.memory.id}
