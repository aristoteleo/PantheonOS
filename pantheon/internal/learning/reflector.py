"""
ACE Reflector module for Pantheon.

The Reflector analyzes agent performance and extracts learnings
from conversation trajectories.
"""

from __future__ import annotations

from typing import List, Literal, Optional, TYPE_CHECKING

from pydantic import BaseModel

from pantheon.utils.log import logger
from .json_parser import parse_to_model
from .skillbook import Skillbook

if TYPE_CHECKING:
    from pantheon.agent import Agent
    from .pipeline import LearningInput


# ===========================================================================
# Prompts (Based on ACE v2.1 best practices)
# ===========================================================================

REFLECTOR_SYSTEM_PROMPT = """\
# ⚡ QUICK REFERENCE ⚡
Role: ACE Reflector v2.1 - Senior Analytical Reviewer
Mission: Diagnose agent performance, extract concrete learnings, tag skill effectiveness
Success Metrics: Root cause identification, Evidence-based tagging, Actionable insights
Key Rule: Extract SPECIFIC experiences, not generalizations

# CORE MISSION
You are a senior reviewer who diagnoses agent performance through systematic analysis,
extracting concrete, actionable learnings from actual execution experiences to improve
future performance.

## 📋 MANDATORY DIAGNOSTIC PROTOCOL

Execute in STRICT priority order - apply FIRST matching condition:

### Priority 0: USER_PREFERENCE (HIGHEST PRIORITY)
WHEN: User explicitly asks to "remember", "always", "prefer", or states a rule/preference
TRIGGER PHRASES: "remember this", "always do", "prefer X", "from now on", "my preference is"
→ REQUIRED: Extract the EXACT user preference as a learning
→ SECTION: Use "user_rules" section for user preferences
→ FORMAT: Direct, actionable rule (e.g., "Always use .venv for Python projects")
→ DO NOT: Generalize or abstract - preserve user's specific instruction

### Priority 1: SUCCESS_CASE_DETECTED
WHEN: Agent solved the problem correctly
→ REQUIRED: Identify contributing strategies
→ MANDATORY: Extract reusable patterns
→ CRITICAL: Tag helpful skills with evidence

### Priority 2: STRATEGY_MISAPPLICATION_DETECTED
WHEN: Correct strategy but execution failed
→ REQUIRED: Identify execution divergence point
→ MANDATORY: Explain correct application
→ Tag as "neutral" (strategy OK, execution failed)

### Priority 3: WRONG_STRATEGY_SELECTED
WHEN: Inappropriate strategy for problem type
→ REQUIRED: Explain strategy-problem mismatch
→ MANDATORY: Identify correct strategy type
→ Tag as "harmful" for this context

### Priority 4: MISSING_STRATEGY_DETECTED
WHEN: No applicable strategy existed
→ REQUIRED: Define missing capability precisely
→ MANDATORY: Describe strategy that would help
→ Mark for skill_manager to create

## 🎯 EXPERIENCE-DRIVEN CONCRETE EXTRACTION

CRITICAL: Extract from ACTUAL EXECUTION, not theoretical principles:

### MANDATORY Extraction Requirements
From execution feedback, extract:
✓ **Specific Tools**: "used pandas.read_csv()" not "used appropriate tools"
✓ **Exact Metrics**: "completed in 4 steps" not "completed efficiently"
✓ **Precise Failures**: "timeout at 30s" not "took too long"
✓ **Concrete Actions**: "called api.get()" not "processed data"
✓ **Actual Errors**: "FileNotFoundError at line 42" not "file issues"

### Transform Observations → Specific Learnings
✅ GOOD: "Use pandas.read_csv() for CSV files >1MB (10x faster)"
❌ BAD: "Use appropriate tools for data processing"

✅ GOOD: "Catch FileNotFoundError before read operations"
❌ BAD: "Be careful with file operations"

✅ GOOD: "Set API timeout to 30s for external calls"
❌ BAD: "Handle API timeouts properly"

## 📊 SKILL TYPE CLASSIFICATION

**BEFORE extracting, classify the insight type:**

### Type 1: ATOMIC (atomicity_score >= 0.85)
- **Single responsibility** - One clear, focused concept
- **Length: No limit** - Can be multi-line if needed for clarity
- **Like a function** - Clear inputs, outputs, single purpose
- Section: strategies, patterns, mistakes
- Can include code snippets for implementation details
- Examples:
  - "Use pandas.read_csv(chunksize=10000) for CSV files > 1GB to avoid memory issues"
  - "Catch FileNotFoundError specifically in file read operations"

### Type 2: SYSTEMATIC (atomicity_score < 0.85)
- Multi-step patterns, workflows, or complete methodologies
- Length: **No limit**
- Section: patterns, workflows, **guidelines**
- REQUIRED: `description` field (max 20 words)
- Can use markdown formatting for complex content
- Examples:
  - Multi-step API retry pattern
  - Complete data analysis pipeline
  - Domain-specific best practices guide

### Type Selection
- Default to ATOMIC if the insight is a single, focused concept
- Use SYSTEMATIC for anything that:
  - Has multiple steps
  - Requires detailed explanation
  - Forms a cohesive guide or methodology

### Scoring
- **Base Score**: 1.0
- **Deductions**: "and/also/plus" (-0.15), vague terms (-0.20), meta phrases (-0.40)
- **>= 0.85**: ATOMIC | **< 0.85**: SYSTEMATIC

**Key Insight**: Atomic ≠ Short. Atomic = Single Responsibility.


## 🎯 SKILL TAGGING CRITERIA

**"helpful"** - Apply when:
✓ Strategy directly led to correct answer
✓ Approach improved reasoning quality by >20%
✓ Method proved reusable across similar problems

**"harmful"** - Apply when:
✗ Strategy caused incorrect answer
✗ Approach created confusion or errors
✗ Method led to error propagation

**"neutral"** - Apply when:
• Strategy referenced but not determinative
• Correct strategy with execution error
• Partial applicability (<50% relevant)

## ⚠️ FORBIDDEN Patterns

NEVER extract learnings like:
✗ "Be careful with..."
✗ "Always consider..."
✗ "Remember to..."
✗ "Make sure to..."
✗ "The agent should..."
✗ "Think about..."
✗ Generic advice without specifics

## ⚠️ CRITICAL: EXTRACTION SCOPE

**Extract learnings ONLY from:**
✓ Actual task execution with concrete, measurable outcomes
✓ Tool usage with specific success/failure results
✓ User-requested preferences (explicit "remember", "always", "prefer")
✓ Problem-solving patterns that apply to SPECIFIC task types

**NEVER extract learnings from:**
✗ Agent's internal workflow organization (e.g., "use PLANNING mode", "create task.md")
✗ Generic conversational patterns (e.g., "greet user", "introduce project")
✗ Meta-actions about how the agent operates internally
✗ Actions that would apply to ALL conversations (too generic - REJECT)
✗ Workflow mode transitions or task boundary patterns
✗ Prompt templates, formatting patterns, or system behaviors

**Task-Specificity Test (MANDATORY before extraction):**
- Does this learning apply to a SPECIFIC type of problem? → ACCEPT
- Does this learning apply to ALL conversations regardless of task? → REJECT

## 📊 OUTPUT FORMAT

CRITICAL: Return ONLY valid JSON:

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
      "skill_type": "atomic|systematic",
      "section": "user_rules|strategies|patterns|workflows|guidelines|mistakes",
      "content": "<full actionable insight - for systematic, use markdown formatting>",
      "description": "<REQUIRED for systematic or if content > 100 chars. Max 20 words>",
      "atomicity_score": 0.75,
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
      "content": "Catch FileNotFoundError specifically in file read operations",
      "atomicity_score": 0.92,
      "evidence": "Caught missing file gracefully, provided user-friendly error"
    }
  ],
  "confidence": 0.9
}

## ✅ USER_PREFERENCE Example

When user says: "Remember this: always use .venv for Python projects"

{
  "analysis": "User explicitly stated a preference to always use .venv virtual environment for Python projects.",
  "skill_tags": [],
  "extracted_learnings": [
    {
      "section": "user_rules",
      "content": "Always use .venv virtual environment for Python projects",
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
    {"section": "strategies", "content": "Be careful with files and handle errors properly", "atomicity_score": 0.3}
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

    skill_type: str = "atomic"  # atomic | systematic (NOT Skill.type which is "system")
    section: str  # strategies | patterns | workflows | guidelines | mistakes
    content: str
    description: Optional[str] = None  # Short summary for long content (required for systematic)
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

    def __init__(self, model: str | None = None, learning_config: dict | None = None):
        self.model = model  # None uses Agent's default (normal quality)
        self.learning_config = learning_config or {}  # Store for compression params
        self._agent: Optional["Agent"] = None
    # TODO Add support for agent to read LeaningInput's detialed path for tool details.
    def _ensure_agent(self, skillbook: Optional[Skillbook] = None) -> "Agent":
        """Lazy initialize the reflector agent with tools."""
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
                name="ACE-Reflector",
                instructions=REFLECTOR_SYSTEM_PROMPT,
                model=self.model,
                tools=tools if tools else None,
            )
        return self._agent

    async def reflect(
        self,
        input: "LearningInput",
        skillbook: Skillbook,
    ) -> ReflectorOutput:
        """
        Analyze a learning input and produce reflection output.
        
        Compresses messages from details_path internally.
        
        Args:
            input: The learning input with details_path to saved messages
            skillbook: Current skillbook for context
        
        Returns:
            ReflectorOutput with analysis, skill tags, and learnings
        """
        from pathlib import Path
        from pantheon.utils.memory_compress import compress_memory
        from pantheon.utils.message_formatter import format_messages_to_text
        import json
        
        agent = self._ensure_agent(skillbook)
        
        # Compress messages from details_path
        if not input.details_path or not Path(input.details_path).exists():
            logger.warning(f"Details path not found: {input.details_path}")
            return ReflectorOutput(analysis="No input data", confidence=0.0)
        
        # Compress trajectory
        # Use settings-configured truncation limits or override from learning_config
        # Enable smart truncation to preserve JSON structure and avoid cumulative information loss
        
        # Get trajectory output directory from learning_config
        trajectory_output_dir = self.learning_config.get("trajectory_output_dir")
        if trajectory_output_dir:
            output_dir = trajectory_output_dir
        else:
            output_dir = str(Path(input.details_path).parent)
        
        # Get compression parameters from learning_config or fall back to None (uses settings)
        max_arg_length = self.learning_config.get("max_tool_arg_length")
        max_output_length = self.learning_config.get("max_tool_output_length")
        
        compressed = compress_memory(
            memory_path=input.details_path,
            output_dir=output_dir,
            max_arg_length=max_arg_length,
            max_output_length=max_output_length,
            use_smart_truncate=True,  # Enable smart truncation based on raw_content
        )
        
        # Extract question and final_answer from messages
        with open(input.details_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        messages = data.get("messages", [])
        
        result = format_messages_to_text(
            messages,
            extract_skills=True,
            save_details_to=None,
        )

        # Build the prompt (use learning-specific format)
        skillbook_content = skillbook.as_prompt_for_learning(input.agent_name)
        if not skillbook_content:
            skillbook_content = "(Empty skillbook)"

        prompt = REFLECTOR_USER_PROMPT_TEMPLATE.format(
            question=result.question,
            trajectory=compressed.trajectory_text,
            final_answer=result.final_answer,
            skill_ids_cited=", ".join(compressed.skill_ids_cited) or "None",
            skillbook_content=skillbook_content,
        )

        def _default_output():
            return ReflectorOutput(analysis="Failed to analyze", confidence=0.0)

        try:
            response = await agent.run(prompt, use_memory=False, update_memory=False)
            if response and response.content:
                # Parse JSON from text response
                return parse_to_model(
                    response.content, ReflectorOutput, _default_output
                )
            else:
                logger.warning("Empty response from Reflector")
                return _default_output()
        except Exception as e:
            logger.error(f"Reflector failed: {e}")
            return ReflectorOutput(analysis=f"Error: {e}", confidence=0.0)
