"""
Special-purpose agents for Pantheon framework.

This module centralizes all agents used for special tasks:
- SummaryGenerator: Summarizes conversation context for sub-agent delegation
- SuggestionGenerator: Generates contextual follow-up questions
- ChatNameGenerator: Generates or updates chat names based on conversation

These agents are used internally by the framework to enhance user experience
and improve sub-agent delegation.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from pantheon.agent import Agent, get_current_run_model
from pantheon.internal.memory import Memory
from pantheon.utils.log import logger, temporary_log_level

# ===== SummaryGenerator =====


class SummaryGenerator:
    """Generate summaries of conversation context for sub-agent delegation.

    This class uses an LLM to extract and summarize key information from parent agent's
    conversation history. The summary is used to provide sub-agents with essential context
    without exposing the full parent conversation history.
    """

    def __init__(self):
        """Initialize SummaryGenerator (lazy-creates summary agent on first use)."""
        self._summary_agent: Optional[Agent] = None
        self._summary_agent_model: str | None = None

    async def generate_summary(
        self,
        messages: list[dict],
        max_tokens: int = 500,
        preferred_model: str | None = None,
    ) -> str:
        """Generate a concise summary of conversation context.

        Extracts key information from message history to provide sub-agents with
        essential context for task execution.

        Args:
            messages: List of message dicts to summarize (conversation history)
            max_tokens: Maximum tokens for the summary

        Returns:
            Summary string (1-3 sentences), or empty string on failure
        """
        if not messages:
            return ""

        # Convert messages to text format for summarization
        context_text = self._format_messages_for_summary(messages)
        if not context_text:
            return ""

        # Generate summary using LLM
        summary = await self._generate_with_llm(context_text, max_tokens, preferred_model)
        return summary or ""

    def _format_messages_for_summary(self, messages: list[dict]) -> str:
        """Format messages for LLM summarization.

        Converts message list into readable text format, filtering out tool outputs.
        """
        text_parts = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Skip system messages and tool messages
            if role == "system" or role == "tool":
                continue

            # Extract content
            if isinstance(content, str):
                if content.strip():
                    text_parts.append(f"{role}: {content}")
            elif isinstance(content, list):  # Content can be array of content blocks
                for block in content:
                    if isinstance(block, dict):
                        block_content = block.get("text", "")
                        if block_content:
                            text_parts.append(f"{role}: {block_content}")

        return "\n".join(text_parts)

    async def _generate_with_llm(
        self,
        context_text: str,
        max_tokens: int,
        preferred_model: str | None = None,
    ) -> str:
        """Use LLM to generate summary of context.

        Args:
            context_text: Formatted conversation context
            max_tokens: Maximum tokens for summary

        Returns:
            Summary string, or empty string on failure
        """
        preferred_model = preferred_model or get_current_run_model()

        # Lazy-create summary agent on first use
        if (
            not self._summary_agent
            or self._summary_agent_model != preferred_model
        ):
            self._summary_agent = Agent(
                name="SummaryGen",
                model=preferred_model,
                instructions="""You are a context summarizer for agent delegation.

Your task: Extract and summarize the parent agent's conversation history to provide sub-agents with essential context for task execution.

Guidelines:
1. Identify and extract KEY INFORMATION:
   - What is the user's main goal/topic?
   - What decisions have been made?
   - What constraints or requirements were mentioned?
   - What is the current status/state?

2. Be CONCISE and FOCUSED:
   - Omit implementation details or tool outputs that don't affect task understanding
   - Keep only information that helps the sub-agent understand the context
   - Maintain logical flow and causality

3. STRUCTURE your summary:
   - Start with the main goal/topic (1-2 sentences)
   - List key decisions or context (bullet points, 2-4 items)
   - End with current state if relevant

4. OUTPUT ONLY the summary text:
   - No preamble, no explanation, no meta-commentary
   - Just clean, usable context for the sub-agent""",
            )
            self._summary_agent_model = preferred_model

        prompt = f"""Please summarize the following conversation context for a sub-agent delegation.
The sub-agent will use this summary as background context for executing a specific task.

Focus on information that affects task execution, not implementation details.

---
CONTEXT TO SUMMARIZE:
{context_text}

---
SUMMARY (concise, maximum {max_tokens} tokens):"""

        try:
            # Suppress debug/info logs during LLM call
            with temporary_log_level("WARNING"):
                response = await self._summary_agent.run(prompt)
            if response:
                content = getattr(response, "content", None) or str(response)
                summary = str(content).strip()
                if summary and len(summary) > 10:
                    return summary
        except Exception as e:
            logger.warning(f"Failed to generate summary: {e}")

        return ""


# ===== SuggestionGenerator =====


@dataclass
class SuggestedQuestion:
    """Suggested follow-up question"""

    text: str
    category: str  # 'clarification', 'follow_up', 'deep_dive', 'related'


class SuggestionGenerator:
    """Centralized manager for generating contextual follow-up questions using a dedicated suggestion agent"""

    def __init__(self):
        """Initialize centralized suggestion manager"""
        self._suggestion_agent: Optional[Agent] = None
        self._suggestion_agent_model: str | None = None
        self._initialization_lock = asyncio.Lock()
        self._is_initialized = False

    async def _ensure_initialized(self, preferred_model: str | None = None):
        """Ensure the suggestion agent is initialized (lazy loading)"""
        preferred_model = preferred_model or get_current_run_model()
        if self._is_initialized and self._suggestion_agent_model == preferred_model:
            return

        async with self._initialization_lock:
            if self._is_initialized and self._suggestion_agent_model == preferred_model:
                return

            await self._initialize_suggestion_agent(preferred_model=preferred_model)
            self._is_initialized = True

    async def _initialize_suggestion_agent(self, preferred_model: str | None = None):
        """Initialize the dedicated suggestion agent"""
        try:
            # Create a simple suggestion agent directly
            self._suggestion_agent = Agent(
                name="Suggestion Agent",
                model=preferred_model,
                instructions="""You are a suggestion assistant that generates contextual follow-up questions.
Your role is to analyze conversation context and suggest 3 relevant questions the user might want to ask next.

Rules:
1. Generate exactly 3 questions that are contextual and actionable
2. Make questions specific to the conversation topic
3. Focus on clarification, follow-up details, or related exploration
4. Keep questions concise and natural
5. Return only the questions, one per line, without numbering or formatting""",
            )
            self._suggestion_agent_model = preferred_model

            if not self._suggestion_agent:
                raise RuntimeError("Failed to create suggestion agent")

            logger.debug("✅ Centralized suggestion agent initialized successfully")

        except Exception as e:
            logger.error(f"❌ Failed to initialize suggestion agent: {str(e)}")
            raise

    async def generate_suggestions(
        self,
        messages: List[Dict[str, Any]],
        max_suggestions: int = 3,
        preferred_model: str | None = None,
    ) -> List[SuggestedQuestion]:
        """
        Generate contextual follow-up questions using the centralized suggestion team

        Args:
            messages: List of chat messages (may include sub-agent messages)
            max_suggestions: Maximum number of suggestions to return

        Returns:
            List of suggested questions
        """
        # Filter out sub-agent messages - only use primary agent messages for suggestions
        agent_messages = messages
        if len(agent_messages) < 2:
            return []

        try:
            # Ensure suggestion agent is initialized
            await self._ensure_initialized(preferred_model=preferred_model)

            if not self._suggestion_agent:
                logger.warning("Suggestion agent not available, skipping suggestions")
                return []

            # Build conversation context from recent primary agent messages only
            context = self._build_conversation_context(agent_messages)
            if not context:
                return []

            # Create prompt for suggestion generation
            prompt = self._build_suggestion_prompt(context, max_suggestions)

            # Generate suggestions using the centralized agent
            try:
                # Suppress debug/info logs during LLM call
                with temporary_log_level("WARNING"):
                    response = await asyncio.wait_for(
                        self._suggestion_agent.run(prompt),
                        timeout=30.0,  # 30 second timeout for suggestions
                    )

                # Parse the response into structured suggestions
                suggestions = self._parse_suggestions(
                    response.content if response else ""
                )

                logger.debug(
                    f"🔮 Generated {len(suggestions)} suggestions using centralized agent"
                )
                return suggestions

            except asyncio.TimeoutError:
                logger.warning("Suggestion generation timed out after 30 seconds")
                return []

        except Exception as e:
            logger.error(f"Error generating centralized suggestions: {str(e)}")
            return []

    def _build_conversation_context(self, messages: List[Dict[str, Any]]) -> str:
        """Build formatted conversation context string from recent messages"""
        # Use last 6 messages for context (same as frontend)
        recent_messages = messages[-6:] if len(messages) > 6 else messages

        context_parts = []
        for msg in recent_messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or msg.get("text", "")

            # Handle different content types
            if isinstance(content, list):
                # Handle multimodal content
                text_content = ""
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_content += item.get("text", "")
                content = text_content

            # Skip empty messages, tool messages, or system messages
            if not content or role in ("tool", "system"):
                continue

            # Truncate very long messages to avoid token limits
            if len(content) > 800:
                content = content[:800] + "..."

            role_label = "User" if role == "user" else "Assistant"
            context_parts.append(f"{role_label}: {content}")

        return "\n\n".join(context_parts)

    def _build_suggestion_prompt(self, context: str, max_suggestions: int) -> str:
        """Build the prompt for suggestion generation"""
        return f"""Based on this conversation, generate {max_suggestions} follow-up questions that the user would ask.

Conversation:
{context}

Generate {max_suggestions} specific questions the user might ask next. Make them contextual and actionable.
Return only the questions, one per line.

Questions:"""

    def _parse_suggestions(self, response_content: str) -> List[SuggestedQuestion]:
        """Parse LLM response into structured suggestion list"""
        if not response_content:
            return []

        suggestions = []
        categories = ["clarification", "follow_up", "deep_dive"]

        for i, line in enumerate(response_content.strip().split("\n")):
            line = line.strip()
            if not line:
                continue

            # Simple cleanup: remove numbers and common prefixes
            if line.startswith(("1.", "2.", "3.", "-", "*")):
                line = line[2:].strip()

            if line:
                suggestions.append(
                    SuggestedQuestion(
                        text=line, category=categories[i % len(categories)]
                    )
                )

            if len(suggestions) >= 3:
                break

        return suggestions


# ===== ChatNameGenerator =====


class ChatNameGenerator:
    """Simple chat name generator with minimal overhead"""

    def __init__(self):
        self._name_agent: Optional[Agent] = None
        self._name_agent_model: str | None = None

    # Default names that indicate the chat hasn't been meaningfully named yet
    DEFAULT_NAMES = {"New Chat", "New Chat in Project", "新建聊天", "在项目中新建聊天", ""}

    async def generate_or_update_name(
        self,
        memory: Memory,
        preferred_model: str | None = None,
    ) -> str:
        """Generate a chat name only if the chat still has a default name.

        Rules:
        - Never rename again once a rename has succeeded (name_generated=True)
        - Only rename if current name is a default placeholder (e.g. "New Chat")
        - Need at least 1 user message to generate a meaningful name
        - Never overwrite a name that was already set (by user or previous generation)
        """
        # If the chat already has a meaningful name, don't touch it. This also
        # covers the hard guard against AI-generated names that happen to look
        # default (e.g. starting with "Chat") — once memory.name is non-default
        # we never rewrite it.
        if not self._is_default_name(memory.name):
            return memory.name

        # If name_generated is set but memory.name is still a default, the
        # rename never actually stuck (e.g. leftover from a prior bug where the
        # name was clobbered back). Fall through and try again.

        agent_messages = memory.get_messages(None)
        user_msgs = [m for m in agent_messages if m.get("role") == "user"]
        if not user_msgs:
            return memory.name

        try:
            new_name = await self._generate_with_ai(
                agent_messages,
                preferred_model=preferred_model,
            )
            if new_name:
                self._update_metadata(memory, len(agent_messages))
                return new_name
        except Exception as e:
            logger.warning(f"AI name generation failed: {e}")

        # Fallback to simple extraction
        fallback = self._fallback_name(agent_messages)
        self._update_metadata(memory, len(agent_messages))
        return fallback

    def _is_default_name(self, name: str) -> bool:
        """Check if a name is a default placeholder that should be replaced.

        Only matches known placeholder variants — "New Chat", "New Chat (2)",
        "新建聊天", etc. Does NOT match arbitrary names starting with "Chat ",
        which was too greedy and caused AI-generated names like "Chat about
        gene expression" to be re-renamed on the next turn.
        """
        if not name:
            return True
        stripped = name.strip()
        if stripped in self.DEFAULT_NAMES:
            return True
        # Numbered placeholders only: "New Chat (2)", "New Chat in Project (3)"
        if stripped.startswith("New Chat (") or stripped.startswith(
            "New Chat in Project ("
        ):
            return True
        # Chinese numbered placeholders: "新建聊天 (2)", "在项目中新建聊天 (3)"
        if stripped.startswith("新建聊天 (") or stripped.startswith(
            "在项目中新建聊天 ("
        ):
            return True
        return False

    async def _generate_with_ai(
        self,
        messages: List[Dict[str, Any]],
        preferred_model: str | None = None,
    ) -> Optional[str]:
        """Generate name using the most informative user messages"""
        preferred_model = preferred_model or get_current_run_model()
        if not self._name_agent or self._name_agent_model != preferred_model:
            self._name_agent = Agent(
                name="ChatNameGen",
                model=preferred_model,
                instructions=(
                    "You are a helpful assistant that generates chat titles with relevant icons. "
                    "Generate a concise (3-6 words) title based on the user's intent. "
                    "Specific rules:\n"
                    "- Start the title with a single relevant emoji (any suitable emoji) WITHOUT a space. The emoji should be highly relevant to the topic (e.g., 🧬, 💻, 📊, 🐞, 📝, etc.).\n"
                    "- Be specific to the technical task or topic.\n"
                    "- Avoid generic words like 'Chat', 'Question', 'Help', 'Analysis'.\n"
                    "- Do not use file extensions unless relevant.\n"
                    "- Return ONLY the emoji and the title text, no quotes or preamble."
                ),
            )
            self._name_agent_model = preferred_model

        # 1. Filter for USER messages only
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return None

        # 2. Informative Strategy: Top 5 longest messages + Last message
        # Exploratory messages (Hi, etc) are short; real tasks are usually longer.
        # This global selection naturally filters 'noise' messages.
        
        # Always include the very last user message for latest focus
        final_msg = user_msgs[-1]
        other_msgs = user_msgs[:-1]

        # Pick top 5 longest from the rest
        msg_info = []
        for i, m in enumerate(other_msgs):
            length = len(str(m.get("_llm_content") or m.get("content") or ""))
            msg_info.append((i, m, length))
            
        # Sort by length descending, take top 5
        msg_info.sort(key=lambda x: x[2], reverse=True)
        selected_info = msg_info[:5]
        
        # Create unique set of selected indices
        selected_msgs_with_index = [(i, m) for i, m, _ in selected_info]
        selected_msgs_with_index.append((len(user_msgs) - 1, final_msg))
        
        # Sort back by original index to keep chronological order
        selected_msgs_with_index.sort(key=lambda x: x[0])
        
        # 3. Format Context
        context_str = ""
        for _, msg in selected_msgs_with_index:
            # Prioritize _llm_content > content
            content = msg.get("_llm_content") or msg.get("content") or ""
            
            if isinstance(content, list):
                # Extract text from multimodal
                text_parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                content = " ".join(text_parts)
            
            context_str += f"- {str(content)[:500]}\n"

        prompt = (
            f"User's Messages defining this chat:\n{context_str}\n"
            f"Generate a concise title (3-6 words) that captures the core user intent:"
        )

        try:
            # Suppress debug/info logs during LLM call
            with temporary_log_level("WARNING"):
                response = await asyncio.wait_for(
                    self._name_agent.run(prompt), timeout=10.0
                )
            if response:
                content = getattr(response, "content", None) or str(response)
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
            if msg.get("role") == "user":
                content = msg.get("_llm_content") or msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    content = " ".join(text_parts)
                
                content = str(content)
                if content:
                    fallback = content[:50].strip()
                    if len(content) > 50:
                        fallback += "..."
                    return fallback
        return f"Chat {datetime.now().strftime('%m-%d %H:%M')}"

    def _update_metadata(self, memory: Memory, message_count: int):
        """Update simple metadata"""
        memory.update_metadata({
            "name_generated": True,
            "last_name_generation_message_count": message_count,
            "last_name_generation_time": datetime.now().isoformat(),
        })


# ===== Singleton instances =====

_summary_generator: Optional[SummaryGenerator] = None
_suggestion_generator: Optional[SuggestionGenerator] = None
_chat_name_generator: Optional[ChatNameGenerator] = None


def get_summary_generator() -> SummaryGenerator:
    """Get the global SummaryGenerator instance"""
    global _summary_generator
    if _summary_generator is None:
        _summary_generator = SummaryGenerator()
    return _summary_generator


def get_suggestion_generator() -> SuggestionGenerator:
    """Get the global SuggestionGenerator instance"""
    global _suggestion_generator
    if _suggestion_generator is None:
        _suggestion_generator = SuggestionGenerator()
    return _suggestion_generator


def get_chat_name_generator() -> ChatNameGenerator:
    """Get the global ChatNameGenerator instance"""
    global _chat_name_generator
    if _chat_name_generator is None:
        _chat_name_generator = ChatNameGenerator()
    return _chat_name_generator
