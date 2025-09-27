"""
Simple Chat Name Generator
Lightweight chat name generation with minimal complexity
"""
from typing import List, Dict, Any, Optional
import asyncio
from datetime import datetime

from ..agent import Agent
from ..memory import Memory
from ..utils.log import logger


class ChatNameGenerator:
    """Simple chat name generator with minimal overhead"""

    def __init__(self):
        self._name_agent: Optional[Agent] = None


    async def generate_or_update_name(self, memory: Memory) -> str:
        """Generate or update chat name - simplified logic"""
        messages = memory.get_messages()

        # Only generate after first conversation (2+ messages)
        if len(messages) < 2:
            return memory.name

        # Check if we should generate/update
        if not self._should_generate_name(memory, messages):
            return memory.name

        try:
            # Try AI generation first
            new_name = await self._generate_with_ai(messages)
            if new_name:
                self._update_metadata(memory, len(messages))
                return new_name
        except Exception as e:
            logger.warning(f"AI name generation failed: {e}")

        # Fallback to simple extraction
        return self._fallback_name(messages)

    def _should_generate_name(self, memory: Memory, messages: List[Dict[str, Any]]) -> bool:
        """Simple logic: generate once after first conversation, update every 6 messages"""
        message_count = len(messages)

        # First generation
        if message_count >= 2 and not memory.extra_data.get("name_generated"):
            return True

        # Periodic update
        last_count = memory.extra_data.get("last_name_generation_message_count", 0)
        if message_count >= last_count + 6:
            return True

        return False

    async def _generate_with_ai(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Simple AI generation with timeout"""
        if not self._name_agent:
            self._name_agent = Agent(
                name="ChatNameGen",
                instructions="Generate a 3-6 word chat title. Return only the title, no quotes or explanation.",
                model="gpt-4o-mini"
            )
            self._name_agent.disable_rich_conversations()

        # Build simple context (last 4 messages)
        context_messages = messages[-4:]
        context = ""
        for msg in context_messages:
            role = "User" if msg.get('role') == 'user' else "AI"
            content = msg.get('content', '')
            if isinstance(content, list):
                # Extract text from multimodal
                text_parts = [item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text']
                content = ' '.join(text_parts)
            if content:
                context += f"{role}: {content[:200]}\n"

        prompt = f"Chat context:\n{context}\nGenerate a short title:"

        try:
            response = await asyncio.wait_for(self._name_agent.run(prompt), timeout=10.0)
            if response:
                content = getattr(response, 'content', None) or str(response)
                name = str(content).strip()
                # Simple cleaning
                if name and len(name) > 3 and len(name) < 100:
                    return name
        except Exception:
            pass

        return None

    def _fallback_name(self, messages: List[Dict[str, Any]]) -> str:
        """Simple fallback: use first user message"""
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, list):
                    text_parts = [item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text']
                    content = ' '.join(text_parts)
                if content:
                    fallback = content[:50].strip()
                    if len(content) > 50:
                        fallback += "..."
                    return fallback
        return f"Chat {datetime.now().strftime('%m-%d %H:%M')}"

    def _update_metadata(self, memory: Memory, message_count: int):
        """Update simple metadata"""
        memory.extra_data["name_generated"] = True
        memory.extra_data["last_name_generation_message_count"] = message_count
        memory.extra_data["last_name_generation_time"] = datetime.now().isoformat()


# Global singleton
_chat_name_generator: Optional[ChatNameGenerator] = None

def get_chat_name_generator() -> ChatNameGenerator:
    """Get global chat name generator instance"""
    global _chat_name_generator
    if _chat_name_generator is None:
        _chat_name_generator = ChatNameGenerator()
    return _chat_name_generator