"""
ACE Reflector module for Pantheon.

The Reflector analyzes agent performance and extracts learnings
from conversation trajectories.
"""

from __future__ import annotations

from typing import List, Literal, Optional, TYPE_CHECKING

from pydantic import BaseModel

from ..agent import Agent
from ..utils.log import logger
from .skillbook import Skillbook

if TYPE_CHECKING:
    from .pipeline import LearningInput


# ===========================================================================
# Prompts (Based on ACE v2.1 best practices)
# ===========================================================================

REFLECTOR_SYSTEM_PROMPT = """\
# ⚡ QUICK REFERENCE ⚡
Role: ACE Reflector - Performance Analyst
Mission: Diagnose agent performance, tag skill effectiveness, extract learnings
Success Metrics: Accurate tagging, Actionable insights, Concrete evidence
Key Rule: Extract SPECIFIC experiences, not generalizations

# CORE MISSION
You analyze an agent's conversation trajectory to:
1. Evaluate if the approach and outcome were effective
2. Tag cited skills [xxx-00001] as helpful/harmful/neutral
3. Extract NEW generalizable learnings for the skillbook

## 📋 DIAGNOSTIC PRIORITY PROTOCOL

Execute in STRICT priority order - apply FIRST matching condition:

### Priority 0: USER_PREFERENCE (HIGHEST PRIORITY)
WHEN: User explicitly asks to "remember", "always", "prefer", or states a rule/preference
TRIGGER PHRASES: "remember this", "always do", "prefer X", "from now on", "my preference is"
→ REQUIRED: Extract the EXACT user preference as a learning
→ SECTION: Use "user_rules" section for user preferences
→ FORMAT: Direct, actionable rule (e.g., "Always use .venv for Python projects")
→ DO NOT: Generalize or abstract - preserve user's specific instruction

### Priority 1: SUCCESS_CASE
WHEN: Agent solved the problem correctly
→ REQUIRED: Identify contributing strategies
→ MANDATORY: Tag helpful skills with evidence
→ EXTRACT: Reusable patterns that worked

### Priority 2: PARTIAL_SUCCESS
WHEN: Correct approach but incomplete/imperfect result
→ REQUIRED: Identify what worked vs what failed
→ TAG: Skills as helpful/neutral based on contribution
→ EXTRACT: Refinement opportunities

### Priority 3: STRATEGY_FAILURE
WHEN: Cited skill led to incorrect outcome
→ REQUIRED: Pinpoint exact failure point
→ MANDATORY: Tag as harmful with justification
→ EXTRACT: What should have been done instead

### Priority 4: MISSING_STRATEGY
WHEN: No applicable skill existed for the problem
→ REQUIRED: Define missing capability precisely
→ MANDATORY: Describe strategy that would help
→ MARK: For skillbook addition

## 🎯 SKILL TAGGING CRITERIA

**"helpful"** - Apply when:
✓ Strategy directly led to correct answer
✓ Approach demonstrably improved outcome
✓ Method proved reusable

**"harmful"** - Apply when:
✗ Strategy caused incorrect answer
✗ Approach created confusion or errors
✗ Method led to wrong path

**"neutral"** - Apply when:
• Strategy referenced but not determinative
• Correct strategy with execution error
• Partial applicability

## 📊 ATOMICITY SCORING FOR LEARNINGS

Score each extracted learning (0.0-1.0):
- **0.95-1.0**: Single atomic concept, immediately actionable
- **0.85-0.95**: Mostly atomic, minor compound elements
- **0.70-0.85**: Acceptable but could be split
- **<0.70**: Too compound or vague - REJECT

### Transform Observations → Specific Learnings
✅ GOOD: "Use pandas.read_csv() for large CSV files (10x faster)"
❌ BAD: "Use appropriate tools for data processing"

✅ GOOD: "Verify file existence before read operations"
❌ BAD: "Be careful with file operations"

## ⚠️ FORBIDDEN Patterns

NEVER extract learnings like:
✗ "Be careful with..."
✗ "Always consider..."
✗ "Remember to..."
✗ "Make sure to..."
✗ "The agent should..."
✗ Generic advice without specifics

## 📊 OUTPUT FORMAT

Return ONLY valid JSON:

{
  "analysis": "<systematic analysis: what happened, why, outcome>",
  "skill_tags": [
    {
      "id": "<skill-id>",
      "tag": "helpful|harmful|neutral",
      "reason": "<specific evidence for this tag>"
    }
  ],
  "extracted_learnings": [
    {
      "section": "user_rules|strategies|patterns|workflows|mistakes",
      "content": "<atomic, actionable insight, max 500 chars>",
      "agent_scope": "global|<agent_name>",
      "atomicity_score": 0.95,
      "evidence": "<specific execution detail>"
    }
  ],
  "confidence": 0.85
}

## ✅ GOOD Example

{
  "analysis": "Agent used file reading skill correctly. Successfully parsed CSV with 10k rows in 0.3s. Error handling worked when file not found.",
  "skill_tags": [
    {"id": "str-00001", "tag": "helpful", "reason": "Guided correct pandas usage, 10x faster than manual parsing"}
  ],
  "extracted_learnings": [
    {
      "section": "patterns",
      "content": "Use try-except around file operations with specific FileNotFoundError handling",
      "agent_scope": "global",
      "atomicity_score": 0.92,
      "evidence": "Caught missing file gracefully, provided user-friendly error"
    }
  ],
  "confidence": 0.9
}

## ✅ USER_PREFERENCE Example

When user says: "Remember this: always use .venv for Python projects"

{
  "analysis": "User explicitly stated a preference to always use .venv virtual environment for Python projects. Agent acknowledged and confirmed the preference.",
  "skill_tags": [],
  "extracted_learnings": [
    {
      "section": "user_rules",
      "content": "Always use .venv virtual environment for Python projects",
      "agent_scope": "global",
      "atomicity_score": 1.0,
      "evidence": "User explicitly requested: 'remember this: always use .venv'"
    }
  ],
  "confidence": 0.95
}

## ❌ BAD Example (DO NOT DO THIS)

{
  "analysis": "The agent did well overall.",
  "skill_tags": [],
  "extracted_learnings": [
    {"section": "strategies", "content": "Be careful with files and handle errors properly"}
  ],
  "confidence": 0.5
}

MANDATORY: Begin response with `{` and end with `}`"""

REFLECTOR_USER_PROMPT_TEMPLATE = """\
## Question
{question}

## Trajectory
{trajectory}

## Final Answer
{final_answer}

## Skills Cited
{skill_ids_cited}

## Current Skillbook
{skillbook_content}

Analyze this trajectory following the diagnostic protocol above."""


# ===========================================================================
# Data Models
# ===========================================================================


class SkillTag(BaseModel):
    """Tag for an existing skill based on its helpfulness."""

    id: str
    tag: Literal["helpful", "harmful", "neutral"]
    reason: str = ""


class ExtractedLearning(BaseModel):
    """A new learning extracted from the trajectory."""

    section: str  # strategies | mistakes | patterns | workflows
    content: str
    agent_scope: str = "global"
    atomicity_score: float = 0.8
    evidence: str = ""


class ReflectorOutput(BaseModel):
    """Output from the Reflector analysis."""

    analysis: str  # Brief analysis of the trajectory
    skill_tags: List[SkillTag] = []  # Tags for cited skills
    extracted_learnings: List[ExtractedLearning] = []  # New learnings
    confidence: float = 0.5  # Confidence in the analysis


# ===========================================================================
# Reflector Class
# ===========================================================================


class Reflector:
    """
    Analyzes agent trajectories to extract learnings and evaluate skills.
    
    Uses an LLM to analyze the conversation trajectory and produce:
    1. Analysis of whether the agent's approach was effective
    2. Tags for any cited skills (helpful/harmful/neutral)
    3. New generalizable learnings to add to the skillbook
    """

    def __init__(self, model: str | None = None):
        self.model = model  # None uses Agent's default (normal quality)
        self._agent: Optional[Agent] = None
    # TODO Add support for agent to read LeaningInput's detialed path for tool details.
    def _ensure_agent(self) -> Agent:
        """Lazy initialize the reflector agent."""
        if self._agent is None:
            self._agent = Agent(
                name="ACE-Reflector",
                instructions=REFLECTOR_SYSTEM_PROMPT,
                model=self.model,
                response_format=ReflectorOutput,
            )
        return self._agent

    async def reflect(
        self,
        input: "LearningInput",
        skillbook: Skillbook,
    ) -> ReflectorOutput:
        """
        Analyze a learning input and produce reflection output.
        
        Args:
            input: The learning input with trajectory and context
            skillbook: Current skillbook for context
        
        Returns:
            ReflectorOutput with analysis, skill tags, and learnings
        """
        agent = self._ensure_agent()

        # Build the prompt
        skillbook_content = skillbook.as_prompt(input.agent_name)
        if not skillbook_content:
            skillbook_content = "(Empty skillbook)"

        prompt = REFLECTOR_USER_PROMPT_TEMPLATE.format(
            question=input.question,
            trajectory=input.trajectory,
            final_answer=input.final_answer,
            skill_ids_cited=", ".join(input.skill_ids_cited) or "None",
            skillbook_content=skillbook_content,
        )

        try:
            response = await agent.run(prompt)
            if response and response.content:
                return response.content
            else:
                logger.warning("Empty response from Reflector")
                return ReflectorOutput(analysis="Failed to analyze", confidence=0.0)
        except Exception as e:
            logger.error(f"Reflector failed: {e}")
            return ReflectorOutput(analysis=f"Error: {e}", confidence=0.0)
