from typing import Callable, List, Dict, Any
import uuid
import asyncio
from datetime import datetime

from ..team import PantheonTeam
from ..memory import Memory
from ..utils.misc import run_func
from ..utils.log import logger
from .suggestion_generator import SuggestionGenerator, SuggestedQuestion


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

        # Initialize suggestion generator
        self.suggestion_generator = SuggestionGenerator(team)
        self.current_suggestions: List[SuggestedQuestion] = []

        # Load cached suggestions if they exist
        self._load_cached_suggestions()

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
            if len(self.memory.get_messages()) == 0:
                # summary to get new name using LLM
                prompt = "Please summarize the question to get a name for the chat: \n"
                prompt += str(self.message)
                prompt += (
                    "\n\nPlease directly return the name, no other text or explanation."
                )

                # Temporarily disable rich conversations to avoid tags in name
                enhanced_states = {}
                for agent_name, agent in self.team.agents.items():
                    enhanced_states[agent_name] = agent.enhanced_flow
                    agent.disable_rich_conversations()

                try:
                    new_name = await self.team.run(
                        prompt, use_memory=False, update_memory=False
                    )
                    self.memory.name = new_name.content
                finally:
                    # Restore original enhanced flow states
                    for agent_name, was_enhanced in enhanced_states.items():
                        if was_enhanced:
                            self.team.agents[agent_name].enable_rich_conversations()

            resp = await self.team.run(
                self.message,
                memory=self.memory,
                process_chunk=self.process_chunk,
                process_step_message=self.process_step_message,
                check_stop=self._check_stop,
            )
            self.response = {
                "success": True,
                "response": resp.content,
                "chat_id": self.memory.id,
            }

            # Generate suggestions after successful conversation (non-blocking)
            task = asyncio.create_task(self._generate_suggestions_and_save())
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

    async def _generate_suggestions(self):
        """Generate suggestion questions after conversation completion"""
        try:
            messages = self.memory.get_messages()
            if len(messages) < 2:
                logger.debug(
                    f"Not enough messages ({len(messages)}) to generate suggestions for chat {self.memory.id}"
                )
                return

            # Check if we already have cached suggestions that are still valid
            cached_suggestions = self.memory.extra_data.get("cached_suggestions")
            last_suggestion_message_count = self.memory.extra_data.get(
                "last_suggestion_message_count", 0
            )

            # Only regenerate if we have new messages since last suggestion generation
            if cached_suggestions and len(messages) <= last_suggestion_message_count:
                logger.debug(
                    f"Using cached suggestions for chat {self.memory.id} ({len(cached_suggestions)} suggestions)"
                )
                self.current_suggestions = [
                    SuggestedQuestion(text=s["text"], category=s["category"])
                    for s in cached_suggestions
                ]
                return

            # Convert messages to the format expected by suggestion generator
            formatted_messages = []
            for msg in messages:
                if hasattr(msg, "to_dict"):
                    formatted_messages.append(msg.to_dict())
                elif isinstance(msg, dict):
                    formatted_messages.append(msg)
                else:
                    # Handle other message formats
                    formatted_messages.append(
                        {
                            "role": getattr(msg, "role", "unknown"),
                            "content": getattr(msg, "content", str(msg)),
                        }
                    )

            suggestions = await self.suggestion_generator.generate_suggestions(
                formatted_messages
            )
            self.current_suggestions = suggestions

            # Cache suggestions in memory for persistence
            self.memory.extra_data["cached_suggestions"] = [
                {"text": s.text, "category": s.category} for s in suggestions
            ]
            self.memory.extra_data["last_suggestion_message_count"] = len(messages)
            self.memory.extra_data["suggestions_generated_at"] = (
                datetime.now().isoformat()
            )

            logger.info(
                f"Generated and cached {len(suggestions)} suggestions for chat {self.memory.id}"
            )
            for i, suggestion in enumerate(suggestions):
                logger.debug(
                    f"Suggestion {i + 1} ({suggestion.category}): {suggestion.text}"
                )

        except Exception as e:
            logger.error(
                f"Failed to generate suggestions for chat {self.memory.id}: {str(e)}"
            )
            self.current_suggestions = []

    def get_suggestions(self) -> List[Dict[str, Any]]:
        """Get current suggestion questions"""
        return [
            {"text": s.text, "category": s.category} for s in self.current_suggestions
        ]

    async def refresh_suggestions(self) -> List[Dict[str, Any]]:
        """Manually refresh suggestion questions"""
        # Clear cached suggestions to force regeneration
        self.memory.extra_data.pop('cached_suggestions', None)
        self.memory.extra_data.pop('last_suggestion_message_count', None)
        
        # Also clear current suggestions to force fresh generation
        self.current_suggestions = []
        
        await self._generate_suggestions()
        return self.get_suggestions()

    async def _generate_suggestions_and_save(self):
        """Generate suggestions and save memory (for background tasks)"""
        await self._generate_suggestions()
        # Note: Memory saving will be handled by the chatroom's memory manager
        # after the thread completes, so we don't need to save here

    def _load_cached_suggestions(self):
        """Load cached suggestions from memory extra_data"""
        try:
            cached_suggestions = self.memory.extra_data.get("cached_suggestions")
            if cached_suggestions:
                self.current_suggestions = [
                    SuggestedQuestion(text=s["text"], category=s["category"])
                    for s in cached_suggestions
                ]
                logger.debug(
                    f"Loaded {len(self.current_suggestions)} cached suggestions for chat {self.memory.id}"
                )
            else:
                logger.debug(f"No cached suggestions found for chat {self.memory.id}")
        except Exception as e:
            logger.error(
                f"Error loading cached suggestions for chat {self.memory.id}: {str(e)}"
            )
            self.current_suggestions = []
