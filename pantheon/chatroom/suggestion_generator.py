"""
Centralized Suggestion Manager for Chat Follow-up Questions
Uses a dedicated suggestion agent/team for all suggestion generation across chats
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import asyncio

from ..agent import Agent
from ..utils.log import logger


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
        self._initialization_lock = asyncio.Lock()
        self._is_initialized = False
    
    async def _ensure_initialized(self):
        """Ensure the suggestion agent is initialized (lazy loading)"""
        if self._is_initialized:
            return
            
        async with self._initialization_lock:
            if self._is_initialized:
                return
                
            await self._initialize_suggestion_agent()
            self._is_initialized = True
    
    async def _initialize_suggestion_agent(self):
        """Initialize the dedicated suggestion agent"""
        try:
            # Create a simple suggestion agent directly
            self._suggestion_agent = Agent(
                name="Suggestion Agent",
                instructions="""You are a suggestion assistant that generates contextual follow-up questions. 
Your role is to analyze conversation context and suggest 3 relevant questions the user might want to ask next.

Rules:
1. Generate exactly 3 questions that are contextual and actionable
2. Make questions specific to the conversation topic
3. Focus on clarification, follow-up details, or related exploration
4. Keep questions concise and natural
5. Return only the questions, one per line, without numbering or formatting""",
                model="gpt-4o-mini",  # Use efficient model for suggestions
            )
            
            if not self._suggestion_agent:
                raise RuntimeError("Failed to create suggestion agent")
            
            # Enable rich conversations for better responses
            self._suggestion_agent.enable_rich_conversations()
            
            logger.info("✅ Centralized suggestion agent initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize suggestion agent: {str(e)}")
            raise
    
    async def generate_suggestions(
        self, 
        messages: List[Dict[str, Any]], 
        max_suggestions: int = 3
    ) -> List[SuggestedQuestion]:
        """
        Generate contextual follow-up questions using the centralized suggestion team
        
        Args:
            messages: List of chat messages
            max_suggestions: Maximum number of suggestions to return
            
        Returns:
            List of suggested questions
        """
        # Check if we have enough messages for suggestions
        if len(messages) < 2:
            return []
        
        try:
            # Ensure suggestion agent is initialized
            await self._ensure_initialized()
            
            if not self._suggestion_agent:
                logger.warning("Suggestion agent not available, skipping suggestions")
                return []
            
            # Build conversation context from recent messages
            context = self._build_conversation_context(messages)
            if not context:
                return []
            
            # Create prompt for suggestion generation
            prompt = self._build_suggestion_prompt(context, max_suggestions)
            
            # Generate suggestions using the centralized agent
            # Temporarily disable rich conversations to avoid tags in suggestions
            was_enhanced = self._suggestion_agent.enhanced_flow
            self._suggestion_agent.disable_rich_conversations()
            
            try:
                # Generate suggestions using the agent directly with timeout
                response = await asyncio.wait_for(
                    self._suggestion_agent.run(prompt), 
                    timeout=30.0  # 30 second timeout for suggestions
                )
                
                # Parse the response into structured suggestions
                suggestions = self._parse_suggestions(response.content if response else "")
                
                logger.info(f"🔮 Generated {len(suggestions)} suggestions using centralized agent")
                return suggestions
                
            except asyncio.TimeoutError:
                logger.warning("Suggestion generation timed out after 30 seconds")
                return []
            finally:
                # Restore original enhanced flow state
                if was_enhanced:
                    self._suggestion_agent.enable_rich_conversations()
                        
        except Exception as e:
            logger.error(f"Error generating centralized suggestions: {str(e)}")
            return []
    
    def _build_conversation_context(self, messages: List[Dict[str, Any]]) -> str:
        """Build formatted conversation context string from recent messages"""
        # Use last 6 messages for context (same as frontend)
        recent_messages = messages[-6:] if len(messages) > 6 else messages
        
        context_parts = []
        for msg in recent_messages:
            role = msg.get('role', '')
            content = msg.get('content', '') or msg.get('text', '')
            
            # Handle different content types
            if isinstance(content, list):
                # Handle multimodal content
                text_content = ""
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        text_content += item.get('text', '')
                content = text_content
            
            # Skip empty messages, tool messages, or system messages
            if not content or role in ('tool', 'system'):
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
        categories = ['clarification', 'follow_up', 'deep_dive']
        
        for i, line in enumerate(response_content.strip().split('\n')):
            line = line.strip()
            if not line:
                continue
                
            # Simple cleanup: remove numbers and common prefixes
            if line.startswith(('1.', '2.', '3.', '-', '*')):
                line = line[2:].strip()
            
            if line:
                suggestions.append(SuggestedQuestion(
                    text=line,
                    category=categories[i % len(categories)]
                ))
            
            if len(suggestions) >= 3:
                break
        
        return suggestions


# Global singleton instance
_suggestion_generator: Optional[SuggestionGenerator] = None


def get_centralized_suggestion_manager() -> SuggestionGenerator:
    """Get the global suggestion generator instance"""
    global _suggestion_generator
    if _suggestion_generator is None:
        _suggestion_generator = SuggestionGenerator()
    return _suggestion_generator