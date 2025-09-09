"""
Suggestion Generator for Chat Follow-up Questions
Based on the same pattern as chat title generation in thread.py
"""
from typing import List, Dict, Any
from dataclasses import dataclass

from ..team import PantheonTeam
from ..utils.log import logger


@dataclass
class SuggestedQuestion:
    """Suggested follow-up question"""
    text: str
    category: str  # 'clarification', 'follow_up', 'deep_dive', 'related'


class SuggestionGenerator:
    """Generate contextual follow-up questions for chat conversations"""
    
    def __init__(self, team: PantheonTeam):
        """
        Initialize suggestion generator
        
        Args:
            team: The team to use for generation
        """
        self.team = team
    
    async def generate_suggestions(
        self, 
        messages: List[Dict[str, Any]], 
        max_suggestions: int = 3
    ) -> List[SuggestedQuestion]:
        """
        Generate contextual follow-up questions using the same pattern as chat title generation
        
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
            # Build conversation context from recent messages
            context = self._build_conversation_context(messages)
            if not context:
                return []
            
            # Create prompt for suggestion generation
            prompt = self._build_suggestion_prompt(context, max_suggestions)
            
            # Generate suggestions using the same pattern as chat title generation
            # Temporarily disable rich conversations to avoid tags in suggestions
            enhanced_states = {}
            for agent_name, agent in self.team.agents.items():
                enhanced_states[agent_name] = agent.enhanced_flow
                agent.disable_rich_conversations()
            
            try:
                # Generate suggestions without using memory to avoid interference
                response = await self.team.run(
                    prompt, 
                    use_memory=False, 
                    update_memory=False
                )
                
                # Parse the response into structured suggestions
                suggestions = self._parse_suggestions(response.content if response else "")
                
                logger.info(f"Generated {len(suggestions)} suggestions from LLM response")
                return suggestions
                
            finally:
                # Restore original enhanced flow states
                for agent_name, was_enhanced in enhanced_states.items():
                    if was_enhanced:
                        self.team.agents[agent_name].enable_rich_conversations()
                        
        except Exception as e:
            logger.error(f"Error generating suggestions: {str(e)}")
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