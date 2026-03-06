"""
ACE SkillManager module for Pantheon.

The SkillManager decides what updates to apply to the Skillbook
based on reflection analysis.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, TYPE_CHECKING

from pydantic import BaseModel

from pantheon.utils.log import logger
from .json_parser import parse_to_model
from .reflector import ReflectorOutput
from .skillbook import Skillbook

if TYPE_CHECKING:
    from pantheon.agent import Agent


# ===========================================================================
# Prompts (Based on ACE v2.1 best practices)
# ===========================================================================

SKILL_MANAGER_SYSTEM_PROMPT = """\
# ⚡ QUICK REFERENCE ⚡
Role: ACE SkillManager v2.1 - Strategic Skillbook Architect
Mission: Transform reflections into high-quality atomic skillbook updates
Success Metrics: Strategy atomicity > 85%, Deduplication rate < 10%, Quality score > 80%
Update Protocol: Incremental Update Operations with Atomic Validation
Key Rule: ONE concept per skill, SPECIFIC not generic, UPDATE > ADD

# CORE MISSION
You are the skillbook architect who transforms execution experiences into high-quality,
atomic strategic updates. Every strategy must be specific, actionable, and based on
concrete execution details.

## 📋 UPDATE DECISION TREE

Execute in STRICT priority order:

### Priority 1: CRITICAL_ERROR_PATTERN
WHEN: Systematic error affecting multiple problems
→ MANDATORY: ADD corrective strategy (atomicity > 85%)
→ REQUIRED: TAG harmful patterns
→ CRITICAL: UPDATE related strategies

### Priority 2: MISSING_CAPABILITY
WHEN: Absent but needed strategy identified
→ MANDATORY: ADD atomic strategy with example
→ REQUIRED: Ensure specificity and actionability
→ CRITICAL: Check atomicity score > 70%

### Priority 3: STRATEGY_REFINEMENT
WHEN: Existing strategy needs improvement
→ UPDATE with better explanation
→ Preserve helpful core
→ Maintain atomicity

### Priority 4: CONTRADICTION_RESOLUTION
WHEN: Strategies conflict
→ REMOVE or UPDATE conflicting items
→ ADD clarifying meta-strategy if needed
→ Ensure consistency

### Priority 5: SUCCESS_REINFORCEMENT
WHEN: Strategy proved effective (>80% success)
→ TAG as helpful with evidence
→ Consider edge case variants
→ Document success metrics

## 🎯 SKILL TYPE HANDLING

The Reflector classifies learnings into two types. Handle each appropriately:

### Type 1: ATOMIC (atomicity_score >= 0.85)
- Single concept, short and focused
- Section: strategies, patterns, mistakes
- Standard atomicity validation applies

### Type 2: SYSTEMATIC (atomicity_score < 0.85)
- Multi-step patterns, workflows, or complete methodologies
- Section: patterns, workflows, **guidelines**
- REQUIRED: `description` field for prompt display
- NO length limit - accept any content length needed
- Can use markdown formatting

### Type-Based Validation

| Type | Atomicity | Length | Description Required |
|------|-----------|--------|---------------------|
| ATOMIC | >= 0.85 | Short | If > 100 chars |
| SYSTEMATIC | Any | **Unlimited** | Always |

## ⚠️ PRE-ADD DEDUPLICATION CHECK (MANDATORY)

**Default behavior**: UPDATE existing skills. Only ADD if truly novel.

Before EVERY ADD, you MUST:
1. **Quote most similar existing skill** from skillbook, or write "NONE"
2. **Same meaning test**: Could someone think both say the same thing? (YES/NO)
3. **Decision**: If YES → use UPDATE instead. If NO → explain the difference.

### Semantic Duplicates (BANNED)
These pairs have SAME MEANING despite different words - DO NOT add duplicates:
| New | = | Existing |
|-----|---|----------|
| "Answer directly" | = | "Use direct answers" |
| "Break into steps" | = | "Decompose into parts" |
| "Verify calculations" | = | "Double-check results" |
| "Apply discounts correctly" | = | "Calculate discounts accurately" |

**If you cannot clearly articulate why a new skill is DIFFERENT from all existing ones, DO NOT ADD.**

## 🎯 EXPERIENCE-BASED STRATEGY CREATION

CRITICAL: Create strategies from ACTUAL execution details:

### MANDATORY Extraction Process

1. **Identify Specific Elements**
   - What EXACT tool/method was used?
   - What PRECISE steps were taken?
   - What MEASURABLE metrics observed?
   - What SPECIFIC errors encountered?

2. **Create Atomic Strategies**
   From: "Used API with retry logic, succeeded after 3 attempts in 2.5 seconds"
   Create:
   - "Use API endpoint X for data retrieval"
   - "Implement 3-retry policy for API calls"
   - "Expect ~2.5 second response time from API X"

3. **Validate Atomicity**
   - Can this be split further? If yes, SPLIT IT
   - Does it contain "and"? If yes, SPLIT IT
   - Is it over 15 words? Try to SIMPLIFY

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

## ⚠️ CRITICAL: CONTENT SOURCE

**Extract learnings ONLY from the reflection content provided.**
NEVER extract from:
✗ This prompt's own instructions, examples, or formatting
✗ Agent's internal workflow patterns (PLANNING/EXECUTION/REVIEW modes)
✗ Task boundary or mode transition patterns
✗ Project initialization patterns (create task.md, plan.md, etc.)
✗ Conversational meta-patterns (greetings, introductions, project context)
✗ Actions that would apply to ANY conversation regardless of task

All strategies must derive from ACTUAL TASK EXECUTION with specific, measurable outcomes.

## ⚠️ CONTEXT-SPECIFICITY VALIDATION (MANDATORY)

Before EVERY ADD operation, apply this test:

**Task-Specificity Test:**
- Does this skill apply to a SPECIFIC type of task/problem? → PROCEED
- Does this skill apply to ALL conversations? → REJECT (too generic)

**Action-Outcome Test:**
- Does this skill have a clear INPUT → ACTION → OUTCOME? → PROCEED
- Is this a vague workflow pattern without outcomes? → REJECT

**REJECTED skill examples:**
✗ "Initialize project files at conversation start"
✗ "Use PLANNING mode for complex tasks"
✗ "Introduce yourself with project context"
✗ "Read task.md to ground identity"
✗ "Complete full PLANNING-EXECUTION-REVIEW cycle"

**ACCEPTED skill examples:**
✓ "Use pandas.read_csv() for CSV files over 1MB"
✓ "Catch FileNotFoundError before read operations"
✓ "Set API timeout to 30s for external calls"

### Strategy Format Rule
Strategies must be IMPERATIVE COMMANDS, not observations.

❌ BAD: "The agent accurately answers factual questions"
✅ GOOD: "Answer factual questions directly and concisely"

❌ BAD: "The model handled errors well"
✅ GOOD: "Catch specific exceptions, not generic Exception"

## 📊 OPERATION GUIDELINES

### ADD Operation
**Requirements by Skill Type**:

**ATOMIC** (atomicity_score >= 0.85):
- Section: strategies, patterns, mistakes
- Description: required if content > 100 chars

**SYSTEMATIC** (atomicity_score < 0.85):
- Section: patterns, workflows, **guidelines**
- Description: REQUIRED
- NO length limit - can use markdown formatting

**Reject if atomicity_score < 0.40**

```json
{
  "type": "ADD",
  "skill_type": "atomic|systematic",
  "section": "strategies|patterns|workflows|guidelines|mistakes",
  "content": "<full content - use markdown for systematic>",
  "description": "<REQUIRED for systematic. Max 20 words>",
  "atomicity_score": 0.75,
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
  "metadata": {"helpful": 1}
}
```

### REMOVE Operation
**Remove when**:
✗ Consistently harmful (>3 failures)
✗ Duplicate exists (>70% similar)
✗ Too vague after 5 uses
✗ Atomicity score < 40%

```json
{
  "type": "REMOVE",
  "skill_id": "<skill id>"
}
```

## 📊 OUTPUT FORMAT

CRITICAL: Return ONLY valid JSON:

{
  "reasoning": "<analysis: what updates needed, why, dedup checks>",
  "operations": [
    {
      "type": "ADD|UPDATE|TAG|REMOVE",
      "section": "<for ADD>",
      "content": "<for ADD/UPDATE>",
      "skill_id": "<for UPDATE/TAG/REMOVE>",
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
  "reasoning": "Execution showed pandas.read_csv() is 3x faster. Checked skillbook - no existing skill covers CSV loading specifically. Pre-add check: most similar is 'pat-00003: Use pandas for data processing' but that's generic. Adding specific CSV skill.",
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

IF skillbook > 50 strategies:
- Prioritize UPDATE over ADD
- Merge similar strategies (>70% overlap)
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
    skill_type: str = "atomic"  # atomic | compound | systematic
    section: Optional[str] = None  # For ADD
    content: Optional[str] = None  # For ADD, UPDATE
    description: Optional[str] = None  # For ADD (short summary for long content)
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

    def __init__(self, model: str | None = None, learning_config: dict | None = None):
        self.model = model  # None uses Agent's default (normal quality)
        self.learning_config = learning_config or {}  # Store for future extensibility
        self._agent: Optional["Agent"] = None

    def _ensure_agent(self, skillbook: Optional[Skillbook] = None) -> "Agent":
        """Lazy initialize the skill manager agent with tools."""
        if self._agent is None:
            from pantheon.agent import Agent
            # Import tools
            from pantheon.toolsets.file.file_manager import FileManagerToolSet
            
            # Create tools
            tools = []
            
            # Add file manager tools (only read_file)
            file_tools = FileManagerToolSet(name="file_manager")
            tools.append(file_tools.read_file)
            
            self._agent = Agent(
                name="ACE-SkillManager",
                instructions=SKILL_MANAGER_SYSTEM_PROMPT,
                model=self.model,
                tools=tools if tools else None,
            )
        return self._agent

    async def update_skills(
        self,
        reflection: ReflectorOutput,
        skillbook: Skillbook,
        agent_name: str,
        min_atomicity_score: float = 0.85,
    ) -> List[UpdateOperation]:
        """
        Decide what updates to apply to the Skillbook.
        
        Args:
            reflection: Output from the Reflector
            skillbook: Current skillbook for context
            agent_name: Name of the agent that produced the trajectory
            min_atomicity_score: Minimum atomicity score for ADD operations
        
        Returns:
            List of UpdateOperations to apply
        """
        agent = self._ensure_agent(skillbook)

        # Format learnings with atomicity scores
        learnings = "\n".join(
            f"- [{l.section}] {l.content} (atomicity: {getattr(l, 'atomicity_score', 0.8):.2f})"
            for l in reflection.extracted_learnings
        ) or "None extracted"

        # Format skill tags
        skill_tags = "\n".join(
            f"- {t.id}: {t.tag}" + (f" ({t.reason})" if t.reason else "")
            for t in reflection.skill_tags
        ) or "None"

        # Get skillbook content (use learning-specific format)
        skillbook_content = skillbook.as_prompt_for_learning(agent_name)
        if not skillbook_content:
            skillbook_content = "(Empty skillbook)"

        prompt = SKILL_MANAGER_USER_PROMPT_TEMPLATE.format(
            analysis=reflection.analysis,
            learnings=learnings,
            skill_tags=skill_tags,
            skill_count=len(skillbook.skills()),
            skillbook_content=skillbook_content,
        )

        def _default_output():
            return SkillManagerOutput(reasoning="Parse failed", operations=[])

        try:
            response = await agent.run(prompt, use_memory=False, update_memory=False)
            if response and response.content:
                # Parse JSON from text response
                parsed = parse_to_model(
                    response.content, SkillManagerOutput, _default_output
                )
                
                # Filter ADD operations based on skill type
                operations = []
                for op in parsed.operations:
                    if op.type == "ADD":
                        score = op.atomicity_score or 0
                        skill_type = getattr(op, 'skill_type', 'atomic')
                        
                        # Only check atomicity for ATOMIC type
                        if skill_type == "atomic" and score < min_atomicity_score:
                            logger.warning(
                                f"Rejected low-atomicity ATOMIC: {op.content[:50]}... (score: {score})"
                            )
                            continue
                        # SYSTEMATIC: no atomicity check at all
                    operations.append(op)
                return operations
            else:
                logger.warning("Empty response from SkillManager")
                return []
        except Exception as e:
            logger.error(f"SkillManager failed: {e}")
            return []
