"""
ACE SkillManager module for Pantheon.

The SkillManager decides what updates to apply to the Skillbook
based on reflection analysis.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from ..agent import Agent
from ..utils.log import logger
from .reflector import ReflectorOutput
from .skillbook import Skillbook


# ===========================================================================
# Prompts (Based on ACE v2.1 best practices)
# ===========================================================================

SKILL_MANAGER_SYSTEM_PROMPT = """\
# ⚡ QUICK REFERENCE ⚡
Role: ACE SkillManager - Skillbook Architect
Mission: Transform reflections into high-quality atomic skillbook updates
Success Metrics: Atomicity > 0.85, No duplicates, Quality > 0.8
Key Rule: ONE concept per skill, SPECIFIC not generic, UPDATE > ADD

# CORE MISSION
You are the skillbook architect who transforms execution experiences into 
high-quality, atomic strategic updates. Every strategy must be specific, 
actionable, and based on concrete execution details.

## 📋 UPDATE DECISION PROTOCOL

Execute in STRICT priority order:

### Priority 1: TAG_EXISTING_SKILLS
WHEN: Reflector provided skill tags
→ MANDATORY: Apply all tags from reflection
→ Use TAG operation with metadata

### Priority 2: UPDATE_OVER_ADD
WHEN: Similar skill exists (>70% semantic overlap)
→ MANDATORY: Use UPDATE instead of ADD
→ Preserve helpful core, refine content

### Priority 3: ADD_NEW_SKILL
WHEN: Truly novel pattern with atomicity > 0.7
→ REQUIRED: Pre-add deduplication check
→ REQUIRED: Atomicity validation

### Priority 4: REMOVE_HARMFUL
WHEN: Skill consistently harmful (harmful > helpful by 3+)
→ Mark for removal with justification

## 🎯 ATOMICITY PRINCIPLE

CRITICAL: Every strategy must represent ONE atomic concept.

### Scoring (0.0-1.0)
- **0.95-1.0**: Single focused concept ✨
- **0.85-0.95**: Mostly atomic, acceptable ✓
- **0.70-0.85**: Should be split ⚡
- **<0.70**: REJECT - too compound ❌

### Breaking Compound → Atomic

**Compound**: "Use pandas for data loading and visualization"
**Split into**:
1. "Use pandas.read_csv() for CSV file loading"
2. "Use matplotlib/seaborn for data visualization"

**Compound**: "Handle errors and log properly"
**Split into**:
1. "Wrap I/O operations in try-except blocks"
2. "Log errors with context: function name, input values"

## ⚠️ PRE-ADD DEDUPLICATION CHECK (MANDATORY)

Before EVERY ADD, you MUST:
1. **Quote most similar existing skill** or write "NONE"
2. **Same meaning test**: Could someone think both say the same thing?
3. **Decision**: If YES → use UPDATE. If NO → explain difference.

### Semantic Duplicates (BANNED)
| New | = | Existing |
|-----|---|----------|
| "Answer directly" | = | "Use direct answers" |
| "Verify calculations" | = | "Double-check results" |
| "Break into steps" | = | "Decompose into parts" |

**If you cannot articulate why new skill differs from existing, DO NOT ADD.**

## ⚠️ FORBIDDEN Strategies

NEVER add strategies saying:
✗ "Be careful with..."
✗ "Always consider..."
✗ "Think about..."
✗ "Remember to..."
✗ "Make sure to..."
✗ "Don't forget..."
✗ Generic advice without specifics
✗ Observations about "the agent" (use imperatives instead)

### Strategy Format Rule
Strategies must be IMPERATIVE COMMANDS, not observations.

❌ BAD: "The agent accurately answers factual questions"
✅ GOOD: "Answer factual questions directly and concisely"

❌ BAD: "The model handled errors well"
✅ GOOD: "Catch specific exceptions, not generic Exception"

## 📊 OPERATION GUIDELINES

### ADD Operation
```json
{
  "type": "ADD",
  "section": "strategies|mistakes|patterns|workflows",
  "content": "<atomic strategy, <15 words, imperative>",
  "agent_scope": "global|<agent_name>",
  "atomicity_score": 0.95,
  "pre_add_check": {
    "most_similar": "<skill_id: content> or NONE",
    "same_meaning": false,
    "difference": "<how this differs>"
  }
}
```

### UPDATE Operation
```json
{
  "type": "UPDATE",
  "skill_id": "<existing skill id>",
  "content": "<improved content>"
}
```

### TAG Operation
```json
{
  "type": "TAG",
  "skill_id": "<skill id>",
  "metadata": {"helpful": 1}  // or {"harmful": 1} or {"neutral": 1}
}
```

### REMOVE Operation
```json
{
  "type": "REMOVE",
  "skill_id": "<skill id>"
}
```

## 📊 OUTPUT FORMAT

Return ONLY valid JSON:

{
  "reasoning": "<analysis: what updates needed, why, dedup checks>",
  "operations": [
    {
      "type": "ADD|UPDATE|TAG|REMOVE",
      "section": "<for ADD>",
      "content": "<for ADD/UPDATE>",
      "skill_id": "<for UPDATE/TAG/REMOVE>",
      "agent_scope": "global",
      "atomicity_score": 0.95,
      "metadata": {"helpful": 1},
      "pre_add_check": {"most_similar": "NONE", "same_meaning": false, "difference": "..."}
    }
  ],
  "quality_metrics": {
    "avg_atomicity": 0.92,
    "operations_count": 2
  }
}

## ✅ HIGH-QUALITY Example

{
  "reasoning": "Reflection showed pandas.read_csv() was 3x faster. No existing skill covers CSV loading specifically. Adding atomic skill.",
  "operations": [
    {
      "type": "TAG",
      "skill_id": "str-00001",
      "metadata": {"helpful": 1}
    },
    {
      "type": "ADD",
      "section": "patterns",
      "content": "Use pandas.read_csv() for CSV files over 1MB",
      "agent_scope": "global",
      "atomicity_score": 0.95,
      "pre_add_check": {
        "most_similar": "pat-00003: Use pandas for data processing",
        "same_meaning": false,
        "difference": "Existing is generic pandas; new is specific to CSV loading with size threshold"
      }
    }
  ],
  "quality_metrics": {"avg_atomicity": 0.95, "operations_count": 2}
}

## ❌ BAD Example (DO NOT DO THIS)

{
  "reasoning": "Should add some skills.",
  "operations": [
    {
      "type": "ADD",
      "section": "strategies",
      "content": "Be careful with data processing and handle errors properly",
      "atomicity_score": 0.35
    }
  ]
}

## 📈 SKILLBOOK SIZE MANAGEMENT

IF skillbook > 50 skills:
- Prioritize UPDATE over ADD
- Merge similar strategies
- Remove lowest-performing skills
- Focus on quality over quantity

MANDATORY: Begin response with `{` and end with `}`"""

SKILL_MANAGER_USER_PROMPT_TEMPLATE = """\
## Reflector Analysis
{analysis}

## Extracted Learnings (from Reflector)
{learnings}

## Skill Tags (from Reflector)
{skill_tags}

## Current Skillbook ({skill_count} skills)
{skillbook_content}

Based on this reflection, decide what updates to apply following the protocol above."""


# ===========================================================================
# Data Models
# ===========================================================================


class PreAddCheck(BaseModel):
    """Deduplication check before ADD operation."""
    
    most_similar: str = "NONE"
    same_meaning: bool = False
    difference: str = ""


class UpdateOperation(BaseModel):
    """Single update operation to apply to the Skillbook."""

    type: Literal["ADD", "UPDATE", "TAG", "REMOVE"]
    section: Optional[str] = None  # For ADD
    content: Optional[str] = None  # For ADD, UPDATE
    skill_id: Optional[str] = None  # For UPDATE, TAG, REMOVE
    agent_scope: Optional[str] = None  # For ADD, default "global"
    atomicity_score: Optional[float] = None  # For ADD
    helpful: Optional[int] = None  # For TAG
    harmful: Optional[int] = None  # For TAG  
    neutral: Optional[int] = None  # For TAG
    pre_add_check: Optional[PreAddCheck] = None  # For ADD


class QualityMetrics(BaseModel):
    """Quality metrics for the operations."""
    
    avg_atomicity: float = 0.0
    operations_count: int = 0


class SkillManagerOutput(BaseModel):
    """Output from the SkillManager."""

    reasoning: str = ""
    operations: List[UpdateOperation] = []
    quality_metrics: Optional[QualityMetrics] = None


# ===========================================================================
# SkillManager Class
# ===========================================================================


class SkillManager:
    """
    Decides what updates to apply to the Skillbook based on reflection.
    
    Uses an LLM to analyze the reflection output and decide on:
    - ADD: Adding new skills from extracted learnings
    - UPDATE: Refining existing skills
    - TAG: Applying tags to skills based on performance
    - REMOVE: Marking consistently harmful skills for removal
    """

    def __init__(self, model: str | None = None):
        self.model = model  # None uses Agent's default (normal quality)
        self._agent: Optional[Agent] = None

    def _ensure_agent(self) -> Agent:
        """Lazy initialize the skill manager agent."""
        if self._agent is None:
            self._agent = Agent(
                name="ACE-SkillManager",
                instructions=SKILL_MANAGER_SYSTEM_PROMPT,
                model=self.model,
                response_format=SkillManagerOutput,
            )
        return self._agent

    async def update_skills(
        self,
        reflection: ReflectorOutput,
        skillbook: Skillbook,
        agent_name: str,
    ) -> List[UpdateOperation]:
        """
        Decide what updates to apply to the Skillbook.
        
        Args:
            reflection: Output from the Reflector
            skillbook: Current skillbook for context
            agent_name: Name of the agent that produced the trajectory
        
        Returns:
            List of UpdateOperations to apply
        """
        agent = self._ensure_agent()

        # Format learnings with atomicity scores
        learnings = "\n".join(
            f"- [{l.section}] {l.content} (scope: {l.agent_scope}, atomicity: {getattr(l, 'atomicity_score', 0.8):.2f})"
            for l in reflection.extracted_learnings
        ) or "None extracted"

        # Format skill tags
        skill_tags = "\n".join(
            f"- {t.id}: {t.tag}" + (f" ({t.reason})" if t.reason else "")
            for t in reflection.skill_tags
        ) or "None"

        # Get skillbook content
        skillbook_content = skillbook.as_prompt(agent_name)
        if not skillbook_content:
            skillbook_content = "(Empty skillbook)"

        prompt = SKILL_MANAGER_USER_PROMPT_TEMPLATE.format(
            analysis=reflection.analysis,
            learnings=learnings,
            skill_tags=skill_tags,
            skill_count=len(skillbook.skills()),
            skillbook_content=skillbook_content,
        )

        try:
            response = await agent.run(prompt)
            if response and response.content:
                # Filter out low-atomicity ADD operations
                operations = []
                for op in response.content.operations:
                    if op.type == "ADD" and op.atomicity_score < 0.7:
                        logger.warning(
                            f"Rejected low-atomicity ADD: {op.content} (score: {op.atomicity_score})"
                        )
                        continue
                    operations.append(op)
                return operations
            else:
                logger.warning("Empty response from SkillManager")
                return []
        except Exception as e:
            logger.error(f"SkillManager failed: {e}")
            return []
